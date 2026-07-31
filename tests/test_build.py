import pytest

from common.config import ArmConfig, CameraConfig, HomeConfig, WorkbenchConfig
from home.client import build_leader
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms
from tests.test_safety_states import make_config
from workbench.server import build_server


def arms():
    return {
        "left": ArmConfig(side="left", serial_number="L", port=None, calibration_id="l"),
        "right": ArmConfig(side="right", serial_number="R", port=None, calibration_id="r"),
    }


def cameras():
    return [
        CameraConfig(id=i, name=f"cam{i}", index=i, width=320, height=240, fps=15, jpeg_quality=80)
        for i in range(3)
    ]


def workbench(use_mock, cams=None):
    return WorkbenchConfig(
        use_mock=use_mock,
        control_port=0,
        video_port=0,
        cameras=cameras() if cams is None else cams,
        safety=make_config(),
        arms=arms(),
    )


def home(use_mock):
    return HomeConfig(
        server_host="127.0.0.1",
        control_port=0,
        video_port=0,
        use_mock=use_mock,
        client_watchdog_ms=300,
        arms=arms(),
    )


def test_mock_server_builds_fake_devices():
    server, publishers = build_server(workbench(use_mock=True))
    try:
        assert isinstance(server._follower, FakeFollowerArms)
        assert len(publishers) == 3
    finally:
        server.stop()
        for p in publishers:
            p.stop()


def test_mock_client_builds_fake_leader():
    leader = build_leader(home(use_mock=True))
    try:
        assert isinstance(leader, FakeLeaderArms)
    finally:
        leader.close()


def test_real_server_builds_real_adapters_without_touching_hardware():
    """조립까지는 하드웨어를 만지지 않아야 한다. 연결은 서버 start() 에서 한다."""
    from workbench.follower_arms import RealFollowerArms

    server, publishers = build_server(workbench(use_mock=False))
    try:
        assert isinstance(server._follower, RealFollowerArms)
        assert server._follower.is_connected is False
        assert len(publishers) == 3
    finally:
        for p in publishers:
            p.stop()


def test_real_client_builds_real_leader_without_touching_hardware():
    from home.leader_arms import RealLeaderArms

    leader = build_leader(home(use_mock=False))
    assert isinstance(leader, RealLeaderArms)
    assert leader.is_connected is False


def test_real_server_requires_arms_config():
    cfg = WorkbenchConfig(
        use_mock=False,
        control_port=0,
        video_port=0,
        cameras=cameras(),
        safety=make_config(),
        arms={},
    )
    with pytest.raises(ValueError, match="arms"):
        build_server(cfg)


def test_real_client_requires_arms_config():
    cfg = HomeConfig(
        server_host="127.0.0.1",
        control_port=0,
        video_port=0,
        use_mock=False,
        client_watchdog_ms=300,
        arms={},
    )
    with pytest.raises(ValueError, match="arms"):
        build_leader(cfg)


def test_server_builds_with_no_cameras_at_all():
    """2단계-A: USB 포트가 팔 4대로 차서 카메라가 없다."""
    server, publishers = build_server(workbench(use_mock=False, cams=[]))
    assert publishers == []
    assert server.video_port is not None  # 영상 서버 자체는 여전히 대기한다


def test_client_accepts_zero_cameras():
    """카메라 없이 팔만 돌릴 때 화면에 빈 칸이 뜨지 않아야 한다."""
    from home.hud import Hud, HudStats

    hud = Hud(cam_ids=[], cam_names={})
    try:
        hud.draw(
            frames={},
            telemetry=None,
            leader_joints=None,
            stats=HudStats(
                rtt_ms=None, lost_packets=0, video_connected=False, telemetry_age_ms=None
            ),
            align_threshold_deg=3.0,
            now=0.0,
        )
    finally:
        hud.close()
