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
