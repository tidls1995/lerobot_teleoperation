import pytest

from common.config import SafetyConfig
from common.protocol import JOINT_NAMES, N_JOINTS, Cmd, ControlPacket, Flag, State
from workbench.safety import SafetyGate

ZEROS = [0.0] * N_JOINTS


def make_config(**overrides) -> SafetyConfig:
    """테스트용 안전 설정.

    그리퍼는 실제로 퍼센트(0~100) 단위이므로 한계도 그렇게 잡는다 (스펙 §4.3).
    다만 ``gripper_max_step`` 은 기본적으로 ``max_step_deg`` 와 같게 둔다. 대부분의
    테스트는 클램프 **기구**를 검증하는 것이고 관절마다 값이 다르면 기대값이
    관절별로 갈라져 읽기 어려워지기 때문이다. 단위가 다르다는 사실 자체는
    test_safety_clamps.py 의 그리퍼 전용 테스트가 명시적으로 값을 넣어 검증한다.
    """
    max_step = overrides.get("max_step_deg", 1.5)
    base = dict(
        align_threshold_deg=3.0,
        max_step_deg=max_step,
        gripper_max_step=max_step,
        gripper_limits=(0.0, 100.0),
        follow_error_deg=15.0,
        follow_error_hold_ms=500,
        watchdog_ms=200,
        joint_limits={
            name: (0.0, 100.0) if name.endswith("gripper") else (-120.0, 120.0)
            for name in JOINT_NAMES
        },
    )
    base.update(overrides)
    return SafetyConfig(**base)


def packet(joints=None, clutch=False, cmd=Cmd.NONE, seq=1) -> ControlPacket:
    return ControlPacket(
        seq=seq, t_send=0.0, clutch=clutch, cmd=cmd, joints=tuple(joints if joints else ZEROS)
    )


def test_starts_disconnected_with_torque_off():
    gate = SafetyGate(make_config())
    result = gate.step(None, ZEROS, now=0.0)
    assert result.state is State.DISCONNECTED
    assert result.torque is False
    assert result.targets is None


def test_first_packet_moves_to_aligning():
    gate = SafetyGate(make_config())
    result = gate.step(packet(), ZEROS, now=0.0)
    assert result.state is State.ALIGNING
    assert result.torque is True
    assert result.targets == pytest.approx(ZEROS)


def test_aligning_holds_the_pose_it_entered_with():
    gate = SafetyGate(make_config())
    start = [10.0] * N_JOINTS
    gate.step(packet(), start, now=0.0)
    # 리더가 멀리 있어도 팔로워는 진입 시점 자세를 유지해야 한다
    result = gate.step(packet(joints=[90.0] * N_JOINTS, seq=2), start, now=0.02)
    assert result.state is State.ALIGNING
    assert result.targets == pytest.approx(start)


def test_does_not_engage_while_alignment_error_is_too_large():
    gate = SafetyGate(make_config())
    gate.step(packet(), ZEROS, now=0.0)
    far = [0.0] * N_JOINTS
    far[3] = 10.0  # 임계값 3도를 넘는 관절이 하나라도 있으면 안 된다
    result = gate.step(packet(joints=far, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ALIGNING


def test_engages_when_aligned_and_clutch_pressed():
    gate = SafetyGate(make_config())
    gate.step(packet(), ZEROS, now=0.0)
    near = [1.0] * N_JOINTS  # 전부 3도 이내
    result = gate.step(packet(joints=near, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ENGAGED


def test_clutch_must_be_a_rising_edge():
    """이미 눌린 채로 정렬에 성공해도 engage 되면 안 된다 (스펙 §5.2)."""
    gate = SafetyGate(make_config())
    far = [0.0] * N_JOINTS
    far[0] = 30.0
    # 클러치를 누른 채 크게 어긋난 상태로 진입
    gate.step(packet(joints=far, clutch=True), ZEROS, now=0.0)
    # 누른 채로 정렬이 맞아떨어져도 engage 되지 않아야 한다
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ALIGNING
    # 놓았다가
    gate.step(packet(joints=ZEROS, clutch=False, seq=3), ZEROS, now=0.04)
    # 다시 누르면 engage
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=4), ZEROS, now=0.06)
    assert result.state is State.ENGAGED


def _engage(gate, now=0.0):
    gate.step(packet(), ZEROS, now=now)
    gate.step(packet(joints=ZEROS, clutch=True, seq=2), ZEROS, now=now + 0.02)
    assert gate.state is State.ENGAGED
    return now + 0.02


def test_releasing_clutch_returns_to_aligning_and_freezes():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(packet(joints=[1.0] * N_JOINTS, clutch=False, seq=3), ZEROS, now=t + 0.02)
    assert result.state is State.ALIGNING
    assert result.targets is not None


def test_watchdog_moves_to_hold_after_timeout():
    gate = SafetyGate(make_config(watchdog_ms=200))
    t = _engage(gate)
    # 190ms: 아직 아님
    assert gate.step(None, ZEROS, now=t + 0.190).state is State.ENGAGED
    # 210ms: HOLD
    result = gate.step(None, ZEROS, now=t + 0.210)
    assert result.state is State.HOLD
    assert result.flags & Flag.WATCHDOG
    assert result.torque is True
    assert "watchdog" in (result.reason or "").lower()


def test_hold_does_not_recover_when_packets_resume():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.step(None, ZEROS, now=t + 0.5)
    assert gate.state is State.HOLD
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=10), ZEROS, now=t + 0.6)
    assert result.state is State.HOLD


def test_hold_never_falls_back_to_disconnected():
    """클라이언트가 완전히 사라져도 토크를 유지해야 한다 (스펙 §5.1)."""
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(None, ZEROS, now=t + 60.0)
    assert result.state is State.HOLD
    assert result.torque is True


def test_reset_command_returns_to_aligning():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.step(None, ZEROS, now=t + 0.5)
    assert gate.state is State.HOLD
    result = gate.step(packet(cmd=Cmd.RESET, seq=20), [5.0] * N_JOINTS, now=t + 0.6)
    assert result.state is State.ALIGNING
    assert result.reason is None
    # 리셋 후에는 현재 실제 자세를 기준으로 다시 잡는다
    assert result.targets == pytest.approx([5.0] * N_JOINTS)


def test_reset_is_ignored_outside_hold():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(packet(cmd=Cmd.RESET, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.state is State.ENGAGED


def test_hold_targets_stay_frozen_even_if_actual_drifts():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    frozen = gate.step(None, ZEROS, now=t + 0.5).targets
    later = gate.step(None, [40.0] * N_JOINTS, now=t + 1.0).targets
    assert later == pytest.approx(frozen)


def test_force_hold_can_be_triggered_from_outside():
    """서보 통신 실패처럼 게이트가 알 수 없는 사유로도 HOLD 를 걸 수 있어야 한다."""
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.force_hold("motor communication failure")
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=9), ZEROS, now=t + 0.02)
    assert result.state is State.HOLD
    assert result.reason == "motor communication failure"


def test_force_hold_still_requires_reset_to_clear():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.force_hold("motor communication failure")
    gate.step(packet(clutch=True, seq=9), ZEROS, now=t + 0.02)
    result = gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.04)
    assert result.state is State.ALIGNING
