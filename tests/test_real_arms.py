import pytest

from common.config import ArmConfig
from common.devices import FollowerArms, LeaderArms
from common.serial_ports import PortInfo
from home.leader_arms import RealLeaderArms
from workbench.follower_arms import RealFollowerArms

PORTS = [
    PortInfo(device="COM7", serial_number="FOLLOWER_L", description="USB Serial"),
    PortInfo(device="COM8", serial_number="FOLLOWER_R", description="USB Serial"),
]


def arm(side, serial_number=None, port=None, calibration_id="cal"):
    return ArmConfig(side=side, serial_number=serial_number, port=port, calibration_id=calibration_id)


def two_arms(**kwargs):
    return {
        "left": arm("left", serial_number="FOLLOWER_L", **kwargs),
        "right": arm("right", serial_number="FOLLOWER_R", **kwargs),
    }


def test_follower_requires_both_sides():
    with pytest.raises(ValueError, match="right"):
        RealFollowerArms(arms={"left": arm("left", serial_number="FOLLOWER_L")})


def test_leader_requires_both_sides():
    with pytest.raises(ValueError, match="left"):
        RealLeaderArms(arms={"right": arm("right", serial_number="FOLLOWER_R")})


def test_constructing_does_not_touch_hardware():
    """생성만으로는 시리얼 포트를 열지 않아야 한다. connect() 가 그 일을 한다."""
    follower = RealFollowerArms(arms=two_arms())
    leader = RealLeaderArms(arms=two_arms())
    assert follower.is_connected is False
    assert leader.is_connected is False


def test_adapters_satisfy_the_device_protocols():
    """1단계의 Protocol 을 실제로 만족해야 서버·클라이언트가 갈아끼울 수 있다."""
    assert isinstance(RealFollowerArms(arms=two_arms()), FollowerArms)
    assert isinstance(RealLeaderArms(arms=two_arms()), LeaderArms)


def test_reading_before_connect_is_an_error():
    with pytest.raises(RuntimeError, match="connect"):
        RealFollowerArms(arms=two_arms()).read_positions()
    with pytest.raises(RuntimeError, match="connect"):
        RealLeaderArms(arms=two_arms()).read_positions()


def test_writing_before_connect_is_an_error():
    with pytest.raises(RuntimeError, match="connect"):
        RealFollowerArms(arms=two_arms()).write_positions([0.0] * 12)


def test_close_before_connect_is_harmless():
    RealFollowerArms(arms=two_arms()).close()
    RealLeaderArms(arms=two_arms()).close()
