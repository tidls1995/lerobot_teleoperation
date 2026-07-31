"""HOLD 에서 리셋했을 때 팔로워를 home 자세로 천천히 되돌리는 절차.

**왜 필요한가 (실물에서 부딪힌 문제):**

HOLD 가 걸린 자세가 리더로 물리적으로 도달 불가능하면(손잡이가 책상에 닿는 등)
정렬 절차를 통과할 방법이 없다. 지금은 서버를 재시작해 팔로워를 늘어뜨리고 손으로
돌리면 되지만, 3단계 원격에서는 집에 있는 사람이 팔로워를 만질 수 없다.

**설계:** HOLD → (R 3초) → HOMING → (스페이스를 누르고 있는 동안 천천히 이동)
→ 도착하면 ALIGNING. 이 시스템의 모든 움직임과 마찬가지로 사람이 키를 누르고
있는 동안만 움직인다.
"""

import dataclasses

import pytest

from common.protocol import JOINT_NAMES, N_JOINTS, Cmd, Flag, State
from tests.test_safety_states import ZEROS, make_config, packet
from workbench.safety import SafetyGate

HOME = {name: (5.0 if not name.endswith("gripper") else 50.0) for name in JOINT_NAMES}
HOME_LIST = [HOME[n] for n in JOINT_NAMES]


def homing_config(**overrides):
    base = make_config()
    fields = dict(home_pose=HOME, homing_max_step=1.0, homing_tolerance=0.5)
    fields.update(overrides)
    return dataclasses.replace(base, **fields)


def to_hold(gate, now=0.0):
    """ENGAGED 를 거쳐 워치독으로 HOLD 까지 몰아넣는다."""
    gate.step(packet(), ZEROS, now=now)
    gate.step(packet(joints=ZEROS, clutch=True, seq=2), ZEROS, now=now + 0.02)
    assert gate.state is State.ENGAGED
    gate.step(None, ZEROS, now=now + 0.5)
    assert gate.state is State.HOLD
    return now + 0.5


def test_reset_enters_homing_when_a_home_pose_is_configured():
    gate = SafetyGate(homing_config())
    t = to_hold(gate)
    result = gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    assert result.state is State.HOMING
    assert result.torque is True


def test_reset_goes_straight_to_aligning_without_a_home_pose():
    """home_pose 를 설정하지 않으면 예전 동작을 유지한다."""
    gate = SafetyGate(make_config())
    t = to_hold(gate)
    result = gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    assert result.state is State.ALIGNING


def test_homing_does_not_move_until_the_clutch_is_pressed_afresh():
    """리셋 직후 클러치가 이미 눌려 있어도 저절로 움직여서는 안 된다."""
    gate = SafetyGate(homing_config())
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, clutch=True, seq=10), ZEROS, now=t + 0.02)
    held = gate.step(packet(clutch=True, seq=11), ZEROS, now=t + 0.04)
    assert held.state is State.HOMING
    assert held.targets == pytest.approx(ZEROS), "누른 채 진입했으면 움직이면 안 된다"


def test_homing_moves_while_the_clutch_is_held():
    gate = SafetyGate(homing_config(homing_max_step=1.0))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    # 상승 에지를 만든다
    r1 = gate.step(packet(clutch=True, seq=11), ZEROS, now=t + 0.04)
    assert r1.targets[0] == pytest.approx(1.0)
    r2 = gate.step(packet(clutch=True, seq=12), ZEROS, now=t + 0.06)
    assert r2.targets[0] == pytest.approx(2.0)


def test_homing_stops_when_the_clutch_is_released():
    gate = SafetyGate(homing_config(homing_max_step=1.0))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    gate.step(packet(clutch=True, seq=11), ZEROS, now=t + 0.04)
    released = gate.step(packet(clutch=False, seq=12), ZEROS, now=t + 0.06)
    assert released.state is State.HOMING
    frozen = list(released.targets)
    still = gate.step(packet(clutch=False, seq=13), ZEROS, now=t + 0.08)
    assert still.targets == pytest.approx(frozen)


def test_homing_resumes_only_on_a_new_press():
    gate = SafetyGate(homing_config(homing_max_step=1.0))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    gate.step(packet(clutch=True, seq=11), ZEROS, now=t + 0.04)
    gate.step(packet(clutch=False, seq=12), ZEROS, now=t + 0.06)
    resumed = gate.step(packet(clutch=True, seq=13), ZEROS, now=t + 0.08)
    assert resumed.targets[0] == pytest.approx(2.0)


def test_homing_uses_the_slower_step_not_the_teleop_step():
    gate = SafetyGate(homing_config(homing_max_step=0.5))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    result = gate.step(packet(clutch=True, seq=11), ZEROS, now=t + 0.04)
    # 조종은 1.5/프레임이지만 호밍은 0.5/프레임이어야 한다
    assert result.targets[0] == pytest.approx(0.5)


def test_homing_finishes_and_hands_over_to_aligning():
    gate = SafetyGate(homing_config(homing_max_step=10.0, homing_tolerance=0.5))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    state, targets = None, None
    for i in range(30):
        result = gate.step(packet(clutch=True, seq=11 + i), ZEROS, now=t + 0.04 + 0.02 * i)
        state, targets = result.state, result.targets
        if state is State.ALIGNING:
            break
    assert state is State.ALIGNING
    # 도착한 자세를 그대로 붙들어야 한다. 실제각(ZEROS)으로 되돌아가면 안 된다.
    assert targets == pytest.approx(HOME_LIST, abs=0.6)


def test_homing_never_leaves_the_joint_limits():
    cfg = homing_config(homing_max_step=10.0)
    cfg.joint_limits["left_shoulder_pan"] = (-2.0, 2.0)
    # home 이 한계 밖이면 클램프에 걸려 도착하지 못한다 -> 설정 단계에서 막지만,
    # 게이트 자체도 한계를 넘겨서는 안 된다.
    gate = SafetyGate(cfg)
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    for i in range(20):
        r = gate.step(packet(clutch=True, seq=11 + i), ZEROS, now=t + 0.04 + 0.02 * i)
        assert r.targets[0] <= 2.0 + 1e-6


def test_a_stuck_arm_during_homing_goes_back_to_hold():
    """호밍 중에 팔이 걸리면 계속 밀어붙이지 않고 HOLD 로 가야 한다."""
    gate = SafetyGate(
        homing_config(homing_max_step=10.0, follow_error_deg=5.0, follow_error_hold_ms=100)
    )
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    stuck = [0.0] * N_JOINTS  # 실제각이 0 에서 안 움직인다
    state = None
    for i in range(30):
        result = gate.step(packet(clutch=True, seq=11 + i), stuck, now=t + 0.04 + 0.05 * i)
        state = result.state
        if state is State.HOLD:
            assert result.flags & Flag.FOLLOW_ERROR
            break
    assert state is State.HOLD


def test_watchdog_applies_during_homing():
    gate = SafetyGate(homing_config())
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    assert gate.state is State.HOMING
    result = gate.step(None, ZEROS, now=t + 0.5)
    assert result.state is State.HOLD
    assert result.flags & Flag.WATCHDOG


def test_homing_does_not_follow_the_leader():
    """호밍은 home 자세로 간다. 리더가 어디 있든 무관하다."""
    gate = SafetyGate(homing_config(homing_max_step=1.0))
    t = to_hold(gate)
    gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.02)
    far_leader = [-90.0] * N_JOINTS
    result = gate.step(packet(joints=far_leader, clutch=True, seq=11), ZEROS, now=t + 0.04)
    # home 은 +5 방향이므로 리더(-90)를 따라갔다면 음수가 된다
    assert result.targets[0] == pytest.approx(1.0)
