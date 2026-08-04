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


# --- 포트는 하나라도 열기 전에 전부 조회해야 한다 ----------------------------
#
# 실측(2026-08-03, 작업대 PC): 서버가 왼팔을 연 뒤 오른팔 포트를 조회하는 시점에
# Windows 장치 열거가 [WinError 87] 로 실패했다. 포트를 열지 않는 경로는 전부
# 성공했다. 조회를 먼저 몰아서 하면 이 상황 자체가 생기지 않는다.
#
# 그것과 별개로, 조회 실패를 하드웨어 접촉 전에 잡는 것이 순서상 맞다. 왼팔을
# 연 뒤 오른팔 조회가 실패하면 왼팔만 통전된 어중간한 상태로 죽는다.


def _record_order(monkeypatch, module_name):
    """resolve 와 open 이 실제로 불린 순서를 기록한다."""
    events = []
    import importlib

    module = importlib.import_module(module_name)

    def fake_resolve(serial_number, port, ports=None):
        events.append(f"resolve:{serial_number}")
        return f"COM_{serial_number}"

    monkeypatch.setattr(module, "resolve_port_spec", fake_resolve)
    return events, module


def test_follower_resolves_both_ports_before_opening_either(monkeypatch):
    events, module = _record_order(monkeypatch, "workbench.follower_arms")

    def fake_connect_one(self, side, port, arm):
        events.append(f"open:{port}")
        return object()

    monkeypatch.setattr(module.RealFollowerArms, "_connect_one", fake_connect_one)

    RealFollowerArms(arms=two_arms()).connect()

    assert events[:2] == ["resolve:FOLLOWER_L", "resolve:FOLLOWER_R"], (
        f"조회 2개가 먼저 끝나야 한다: {events}"
    )
    assert all(e.startswith("open:") for e in events[2:]), f"열기는 그 뒤에: {events}"


def test_leader_resolves_both_ports_before_opening_either(monkeypatch):
    events, module = _record_order(monkeypatch, "home.leader_arms")

    def fake_connect_one(self, side, port, arm):
        events.append(f"open:{port}")
        return object()

    monkeypatch.setattr(module.RealLeaderArms, "_connect_one", fake_connect_one)

    RealLeaderArms(arms=two_arms()).connect()

    assert events[:2] == ["resolve:FOLLOWER_L", "resolve:FOLLOWER_R"], (
        f"조회 2개가 먼저 끝나야 한다: {events}"
    )
    assert all(e.startswith("open:") for e in events[2:]), f"열기는 그 뒤에: {events}"


def test_a_lookup_failure_opens_nothing(monkeypatch):
    """오른팔 조회가 실패하면 왼팔도 열려서는 안 된다."""
    from common.serial_ports import PortLookupError

    events, module = _record_order(monkeypatch, "workbench.follower_arms")

    def failing_resolve(serial_number, port, ports=None):
        events.append(f"resolve:{serial_number}")
        if serial_number == "FOLLOWER_R":
            raise PortLookupError("no such port")
        return f"COM_{serial_number}"

    monkeypatch.setattr(module, "resolve_port_spec", failing_resolve)

    def fake_connect_one(self, side, port, arm):
        events.append(f"open:{port}")
        return object()

    monkeypatch.setattr(module.RealFollowerArms, "_connect_one", fake_connect_one)

    with pytest.raises(PortLookupError):
        RealFollowerArms(arms=two_arms()).connect()

    assert not [e for e in events if e.startswith("open:")], f"아무것도 열지 않아야 한다: {events}"


# --- lerobot 없는 리더 어댑터 ---------------------------------------------------
#
# exe 로 배포하려면 클라이언트에서 lerobot 을 걷어내야 한다 (torch 4.2GB). 갈아끼운
# 구현이 같은 규약과 같은 순서를 지키는지, lerobot 판과 같은 잣대로 확인한다.
# 숫자가 같다는 것은 tools/compare_read.py 로 실물에서 따로 확인했다 (차이 0.000).

from home.leader_arms_lite import LiteLeaderArms


def test_lite_leader_requires_both_sides():
    with pytest.raises(ValueError, match="left"):
        LiteLeaderArms(arms={"right": arm("right", serial_number="FOLLOWER_R")})


def test_lite_leader_constructing_does_not_touch_hardware():
    assert LiteLeaderArms(arms=two_arms()).is_connected is False


def test_lite_leader_satisfies_the_device_protocol():
    """서버·클라이언트가 갈아끼울 수 있어야 한다."""
    assert isinstance(LiteLeaderArms(arms=two_arms()), LeaderArms)


def test_lite_leader_reading_before_connect_is_an_error():
    with pytest.raises(RuntimeError, match="connect"):
        LiteLeaderArms(arms=two_arms()).read_positions()


def test_lite_leader_close_before_connect_is_harmless():
    LiteLeaderArms(arms=two_arms()).close()


def test_lite_leader_resolves_both_ports_before_opening_either(monkeypatch):
    """WinError 87 대책은 구현을 갈아끼워도 유지되어야 한다."""
    events, module = _record_order(monkeypatch, "home.leader_arms_lite")

    class FakeBus:
        def __init__(self, port):
            self.port = port

        def read_calibration(self):
            return {}

    def fake_connect_one(self, side, port):
        events.append(f"open:{port}")
        return FakeBus(port)

    monkeypatch.setattr(module.LiteLeaderArms, "_connect_one", fake_connect_one)
    monkeypatch.setattr(module.LiteLeaderArms, "_warn_if_uncalibrated", lambda self, side: None)

    LiteLeaderArms(arms=two_arms()).connect()

    assert events[:2] == ["resolve:FOLLOWER_L", "resolve:FOLLOWER_R"], (
        f"조회 2개가 먼저 끝나야 한다: {events}"
    )
    assert all(e.startswith("open:") for e in events[2:]), f"열기는 그 뒤에: {events}"


def test_lite_leader_warns_about_an_uncalibrated_arm(caplog):
    """캘리브레이션 안 된 팔로 조종하면 팔로워가 엉뚱한 자세로 간다.

    원격 사용자는 캘리브레이션을 안 했을 수 있으므로, 조용히 넘어가면 안 된다.
    """
    import logging

    from common.feetech_lite import MotorCalibration

    leader = LiteLeaderArms(arms=two_arms())
    leader._calibration["left"] = {
        i: MotorCalibration(id=i, homing_offset=0, range_min=0, range_max=4095) for i in range(1, 7)
    }
    with caplog.at_level(logging.WARNING):
        leader._warn_if_uncalibrated("left")
    assert "never calibrated" in caplog.text
    assert "shoulder_pan" in caplog.text


def test_lite_leader_stays_quiet_about_a_calibrated_arm(caplog):
    import logging

    from common.feetech_lite import MotorCalibration

    leader = LiteLeaderArms(arms=two_arms())
    leader._calibration["left"] = {
        i: MotorCalibration(id=i, homing_offset=-1651, range_min=768, range_max=3256)
        for i in range(1, 7)
    }
    with caplog.at_level(logging.WARNING):
        leader._warn_if_uncalibrated("left")
    assert caplog.text == ""


def test_lite_leader_puts_each_joint_in_the_right_slot_with_the_right_unit():
    """자리가 틀리면 어깨 명령이 손목에 들어간다. 그리퍼만 퍼센트다 (스펙 §4.3)."""
    from common.feetech_lite import MotorCalibration
    from common.joints import GRIPPER_INDICES

    # 범위를 0~4094 로 두면 가운데(2047)가 0도, 그리퍼는 50% 가 된다.
    cal = {i: MotorCalibration(id=i, homing_offset=1, range_min=0, range_max=4094) for i in range(1, 7)}

    class FakeBus:
        def __init__(self, base):
            self._base = base

        def sync_read_positions(self):
            # 모터마다 다른 값을 줘서 자리가 섞이면 드러나게 한다.
            return {i: self._base + i for i in range(1, 7)}

        def close(self):
            pass

    leader = LiteLeaderArms(arms=two_arms())
    leader._buses = {"left": FakeBus(2047), "right": FakeBus(3000)}
    leader._calibration = {"left": cal, "right": cal}

    values = leader.read_positions()
    assert len(values) == 12

    # 왼팔이 앞, 오른팔이 뒤. 왼팔 값이 더 작아야 한다 (base 2047 < 3000).
    assert values[0] < values[6]

    # 각 팔 안에서 id 순서대로 조금씩 커진다.
    degrees_left = values[0:5]
    assert degrees_left == sorted(degrees_left), f"자리가 섞였다: {degrees_left}"

    # 그리퍼 두 칸만 퍼센트(0~100)다. 도였다면 음수이거나 100 을 넘었을 것이다.
    for index in GRIPPER_INDICES:
        assert 0.0 <= values[index] <= 100.0, f"{index}번은 퍼센트여야 한다: {values[index]}"

    # 가운데를 읽은 왼팔 첫 관절은 0도 근처다.
    assert abs(values[0]) < 1.0
