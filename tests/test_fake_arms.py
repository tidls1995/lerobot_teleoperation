import pytest

from common.protocol import N_JOINTS
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms


def test_follower_starts_at_initial_pose():
    arms = FakeFollowerArms(initial=[3.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([3.0] * N_JOINTS)


def test_follower_defaults_to_zeros():
    assert FakeFollowerArms().read_positions() == pytest.approx([0.0] * N_JOINTS)


def test_follower_reaches_command_immediately_without_lag():
    arms = FakeFollowerArms()
    arms.write_positions([7.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([7.0] * N_JOINTS)


def test_follower_lags_toward_command():
    arms = FakeFollowerArms(lag=0.5)
    arms.write_positions([10.0] * N_JOINTS)
    assert arms.read_positions()[0] == pytest.approx(5.0)
    arms.write_positions([10.0] * N_JOINTS)
    assert arms.read_positions()[0] == pytest.approx(7.5)


def test_blocked_joint_cannot_pass_its_limit():
    """추종 오차 로직을 검증하기 위한 '팔이 걸림' 시뮬레이션."""
    arms = FakeFollowerArms(blocks={2: 5.0})
    arms.write_positions([50.0] * N_JOINTS)
    pos = arms.read_positions()
    assert pos[2] == pytest.approx(5.0)
    assert pos[1] == pytest.approx(50.0)


def test_torque_flag_is_tracked():
    arms = FakeFollowerArms()
    assert arms.torque is False
    arms.set_torque(True)
    assert arms.torque is True


def test_follower_rejects_wrong_joint_count():
    with pytest.raises(ValueError):
        FakeFollowerArms().write_positions([0.0] * 3)


def test_leader_is_static_until_motion_enabled():
    clock = iter([0.0, 1.0, 2.0, 3.0])
    arms = FakeLeaderArms(base=[2.0] * N_JOINTS, clock=lambda: next(clock))
    assert arms.read_positions() == pytest.approx([2.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([2.0] * N_JOINTS)


def test_leader_moves_when_motion_enabled():
    t = [0.0]
    arms = FakeLeaderArms(base=[0.0] * N_JOINTS, amplitude_deg=20.0, period_s=8.0, clock=lambda: t[0])
    arms.motion_enabled = True
    first = arms.read_positions()
    t[0] = 2.0
    second = arms.read_positions()
    assert first != pytest.approx(second)
    assert all(abs(v) <= 20.0 + 1e-6 for v in second)


def test_leader_joints_are_out_of_phase():
    arms = FakeLeaderArms(base=[0.0] * N_JOINTS, clock=lambda: 1.0)
    arms.motion_enabled = True
    pos = arms.read_positions()
    assert len(set(round(v, 6) for v in pos)) > 1
