import pytest

from common.config import WorkbenchConfig
from mock.fake_arms import FakeFollowerArms
from mock.fake_cameras import FakeCamera
from tests.test_safety_states import make_config
from tools.check_link import LinkResult, check_control, check_video
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.server import TeleopServer


@pytest.fixture
def server():
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    srv = TeleopServer(cfg=cfg, follower=FakeFollowerArms(), video=None)
    srv.start()
    yield srv
    srv.stop()


def test_control_check_succeeds_against_a_live_server(server):
    result = check_control("127.0.0.1", server.control_port, timeout=3.0)
    assert result.ok is True
    assert "ALIGNING" in result.detail


def test_control_check_reports_no_reply_when_nothing_is_listening():
    """Windows 는 닫힌 UDP 포트에 대해 타임아웃이 아니라 ConnectionResetError 를
    던진다. 어느 경로로 오든 'no reply' 로 보고해야 사용자가 헷갈리지 않는다."""
    # 1024 미만 포트에는 우리 서버가 있을 수 없다
    result = check_control("127.0.0.1", 1, timeout=0.5)
    assert result.ok is False
    assert "no reply" in result.detail.lower()


def test_control_check_does_not_move_the_arm(server):
    """진단은 팔을 움직여서는 안 된다. clutch=0 이므로 ALIGNING 에 머문다."""
    from common.protocol import State

    check_control("127.0.0.1", server.control_port, timeout=3.0)
    assert server.state is State.ALIGNING


def test_video_check_succeeds_against_a_live_video_server():
    pub = CameraPublisher(
        camera=FakeCamera(cam_id=0, name="c", width=64, height=48),
        cam_id=0,
        fps=15,
        jpeg_quality=70,
    )
    pub.start()
    vs = VideoServer(port=0, publishers=[pub])
    vs.start()
    try:
        result = check_video("127.0.0.1", vs.port, timeout=5.0)
        assert result.ok is True
        assert "cam" in result.detail.lower()
    finally:
        vs.stop()
        pub.stop()


def test_video_check_reports_refused_when_nothing_is_listening():
    result = check_video("127.0.0.1", 1, timeout=0.5)
    assert result.ok is False
    assert result.detail


def test_link_result_is_falsy_friendly():
    assert LinkResult(ok=True, detail="x").ok is True
    assert LinkResult(ok=False, detail="y").ok is False
