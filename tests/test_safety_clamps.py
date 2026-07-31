import pytest

from common.protocol import N_JOINTS, Cmd, ControlPacket, Flag, State
from tests.test_safety_states import ZEROS, make_config, packet
from workbench.safety import SafetyGate


def engage(gate, actual=None, now=0.0):
    a = actual if actual is not None else ZEROS
    gate.step(packet(joints=tuple(a)), a, now=now)
    gate.step(packet(joints=tuple(a), clutch=True, seq=2), a, now=now + 0.02)
    assert gate.state is State.ENGAGED
    return now + 0.02


def test_speed_clamp_limits_a_large_jump():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    far = [30.0] * N_JOINTS
    result = gate.step(packet(joints=far, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets == pytest.approx([1.5] * N_JOINTS)
    assert result.flags & Flag.SPEED_CLAMPED


def test_speed_clamp_ramps_over_multiple_frames():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    far = [30.0] * N_JOINTS
    seen = []
    for i in range(4):
        r = gate.step(packet(joints=far, clutch=True, seq=3 + i), ZEROS, now=t + 0.02 * (i + 1))
        seen.append(r.targets[0])
    assert seen == pytest.approx([1.5, 3.0, 4.5, 6.0])


def test_small_moves_are_not_clamped():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    near = [0.5] * N_JOINTS
    result = gate.step(packet(joints=near, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets == pytest.approx(near)
    assert not (result.flags & Flag.SPEED_CLAMPED)


def test_joint_limit_clamps_target():
    cfg = make_config(max_step_deg=100.0)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 50.0
    result = gate.step(packet(joints=over, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets[0] == pytest.approx(10.0)
    assert result.flags & Flag.JOINT_LIMITED


def test_joint_limit_applies_before_speed_clamp():
    """한계 밖 목표를 향해 속도 제한만큼만 나아가야 한다."""
    cfg = make_config(max_step_deg=1.5)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 50.0
    result = gate.step(packet(joints=over, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets[0] == pytest.approx(1.5)
    assert result.flags & Flag.JOINT_LIMITED
    assert result.flags & Flag.SPEED_CLAMPED


# 추종 오차는 '지난 프레임에 쓴 명령각'과 '지금 실제각'을 비교한다.
# 따라서 명령을 처음 보낸 프레임에는 아직 오차가 없고, 그 다음 프레임부터 나타난다.
# 아래 테스트들이 한 스텝씩 더 진행하는 이유가 이것이다.
FAR = [40.0] * N_JOINTS


def test_follow_error_warns_but_does_not_hold_immediately():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    stuck = [0.0] * N_JOINTS  # 실제각은 0 에서 멈춰 있다 (팔이 걸림)

    first = gate.step(packet(joints=FAR, clutch=True, seq=3), stuck, now=t + 0.10)
    assert not (first.flags & Flag.FOLLOW_ERROR)  # 명령을 막 보낸 프레임

    second = gate.step(packet(joints=FAR, clutch=True, seq=4), stuck, now=t + 0.12)
    assert second.state is State.ENGAGED
    assert second.flags & Flag.FOLLOW_ERROR


def test_follow_error_holds_after_sustained_period():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    stuck = [0.0] * N_JOINTS

    gate.step(packet(joints=FAR, clutch=True, seq=3), stuck, now=t + 0.10)  # 명령 전달
    gate.step(packet(joints=FAR, clutch=True, seq=4), stuck, now=t + 0.12)  # 오차 감지 시작
    result = gate.step(packet(joints=FAR, clutch=True, seq=5), stuck, now=t + 0.70)  # 580ms 지속
    assert result.state is State.HOLD
    assert "follow" in (result.reason or "").lower()


def test_follow_error_timer_resets_when_error_clears():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    caught_up = list(FAR)

    gate.step(packet(joints=FAR, clutch=True, seq=3), ZEROS, now=t + 0.10)
    gate.step(packet(joints=FAR, clutch=True, seq=4), ZEROS, now=t + 0.12)  # 오차 타이머 시작
    # 팔이 따라잡았다 -> 타이머가 초기화되어야 한다
    gate.step(packet(joints=FAR, clutch=True, seq=5), caught_up, now=t + 0.30)
    result = gate.step(packet(joints=FAR, clutch=True, seq=6), caught_up, now=t + 0.90)
    assert result.state is State.ENGAGED
    assert not (result.flags & Flag.FOLLOW_ERROR)


def test_targets_never_exceed_joint_limits_over_a_long_run():
    cfg = make_config(max_step_deg=1.5)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 1000.0
    for i in range(200):
        r = gate.step(packet(joints=over, clutch=True, seq=3 + i), ZEROS, now=t + 0.02 * (i + 1))
        if r.state is not State.ENGAGED:
            break
        assert r.targets[0] <= 10.0 + 1e-6
