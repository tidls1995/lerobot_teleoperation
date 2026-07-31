import time

import pytest

from common.config import WorkbenchConfig
from common.protocol import N_JOINTS, Flag, State
from home.client import ControlLink
from home.video_recv import VideoClient
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms
from mock.fake_cameras import FakeCamera
from tests.test_safety_states import make_config
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.server import TeleopServer


def wait_until(predicate, timeout=10.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def stack():
    """서버 + 클라이언트 전체를 localhost 에 띄운다."""
    arms = FakeFollowerArms()
    pubs = [
        CameraPublisher(
            camera=FakeCamera(cam_id=i, name=f"cam{i}", width=64, height=48),
            cam_id=i,
            fps=15,
            jpeg_quality=70,
        )
        for i in range(3)
    ]
    video_server = VideoServer(port=0, publishers=pubs)
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=video_server)
    for p in pubs:
        p.start()
    server.start()

    link = ControlLink(host="127.0.0.1", port=server.control_port)
    link.start()
    video_client = VideoClient(host="127.0.0.1", port=video_server.port, reconnect_delay=0.1)
    video_client.start()

    yield server, arms, link, video_client

    link.stop()
    video_client.stop()
    server.stop()
    for p in pubs:
        p.stop()


def _telemetry_state(link):
    got = link.latest_telemetry()
    return got[0].state if got else None


def _send_and_check(link, leader, expected, clutch=False):
    link.send(joints=leader.read_positions(), clutch=clutch, reset=False)
    return _telemetry_state(link) is expected


def test_end_to_end_reaches_aligning(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))


def test_end_to_end_engages_and_follows(stack):
    _, arms, link, _ = stack
    leader = FakeLeaderArms()

    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    # 클러치 상승 에지를 만들기 위해 놓았다 누른다
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    time.sleep(0.05)
    assert wait_until(lambda: _send_and_check(link, leader, State.ENGAGED, clutch=True))

    leader.motion_enabled = True
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        link.send(joints=leader.read_positions(), clutch=True, reset=False)
        time.sleep(1 / 60)

    # 팔로워가 실제로 원점에서 벗어나 움직였어야 한다
    assert max(abs(v) for v in arms.read_positions()) > 1.0
    assert _telemetry_state(link) is State.ENGAGED


def test_rtt_is_measured(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    for _ in range(30):
        link.send(joints=leader.read_positions(), clutch=False, reset=False)
        time.sleep(1 / 60)
    assert wait_until(lambda: link.rtt_ms is not None)
    assert 0.0 <= link.rtt_ms < 100.0  # localhost


def test_video_arrives_on_all_three_cameras(stack):
    _, _, _, video = stack
    assert wait_until(lambda: all(video.latest(i) is not None for i in range(3)))
    assert video.connected is True


def test_watchdog_holds_when_client_stops_sending(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    time.sleep(0.05)
    assert wait_until(lambda: _send_and_check(link, leader, State.ENGAGED, clutch=True))

    time.sleep(0.4)  # watchdog_ms=200
    link.send(joints=leader.read_positions(), clutch=True, reset=False)
    assert wait_until(lambda: _telemetry_state(link) is State.HOLD)
    got = link.latest_telemetry()
    assert got[0].flags & Flag.WATCHDOG


def test_reset_recovers_the_stack(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    time.sleep(0.4)
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    assert wait_until(lambda: _telemetry_state(link) is State.HOLD)

    link.send(joints=leader.read_positions(), clutch=False, reset=True)
    assert wait_until(lambda: _telemetry_state(link) is State.ALIGNING)


def test_blocked_arm_triggers_follow_error_hold():
    """팔이 걸리면 HOLD 로 가야 한다 (물리 사고 방어)."""
    arms = FakeFollowerArms(blocks={2: 1.0})
    cfg = WorkbenchConfig(
        use_mock=True,
        control_port=0,
        video_port=0,
        cameras=[],
        safety=make_config(follow_error_deg=5.0, follow_error_hold_ms=200, max_step_deg=50.0),
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    link = ControlLink(host="127.0.0.1", port=server.control_port)
    link.start()
    try:
        zeros = [0.0] * N_JOINTS

        def pump(clutch):
            link.send(joints=zeros, clutch=clutch, reset=False)
            return _telemetry_state(link)

        assert wait_until(lambda: pump(False) is State.ALIGNING)
        assert wait_until(lambda: pump(True) is State.ENGAGED)

        far = [0.0] * N_JOINTS
        far[2] = 60.0  # 2번 관절은 1.0 도에서 막혀 있다
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _telemetry_state(link) is not State.HOLD:
            link.send(joints=far, clutch=True, reset=False)
            time.sleep(1 / 60)
        assert _telemetry_state(link) is State.HOLD
    finally:
        link.stop()
        server.stop()
