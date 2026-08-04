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


def test_snapshot_returns_one_packets_values_together(stack):
    """소킹은 '언제 튀었나'를 세므로, 서로 다른 패킷의 값이 섞이면 안 된다.

    latest_telemetry() 와 rtt_ms 를 따로 부르면 그 사이에 새 패킷이 들어와
    튄 RTT 가 엉뚱한 시각에 기록될 수 있다.
    """
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert link.snapshot() is None, "패킷을 받기 전에는 None 이어야 한다"

    for _ in range(30):
        link.send(joints=leader.read_positions(), clutch=False, reset=False)
        time.sleep(1 / 60)
    assert wait_until(lambda: link.snapshot() is not None and link.snapshot()[2] is not None)

    packet, recv_at, rtt_ms, lost = link.snapshot()
    assert packet.seq_echo > 0
    assert recv_at > 0.0
    assert 0.0 <= rtt_ms < 100.0  # localhost
    assert lost >= 0

    # 수신 시각으로 중복을 거른다: 새 패킷이 오면 시각이 바뀐다.
    first_at = recv_at
    assert wait_until(
        lambda: (
            link.send(joints=leader.read_positions(), clutch=False, reset=False),
            link.snapshot()[1] != first_at,
        )[1]
    )


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


# --- 제어 송신과 화면 그리기의 분리 (실행 중 발견한 결함) --------------------


def test_command_state_reset_fires_exactly_once():
    from home.client import CommandState

    cs = CommandState()
    cs.request_reset()
    assert cs.take() == (False, True)
    assert cs.take() == (False, False)


def test_command_state_clutch_is_level_not_edge():
    from home.client import CommandState

    cs = CommandState()
    cs.set_clutch(True)
    assert cs.take()[0] is True
    assert cs.take()[0] is True  # 읽어도 유지된다
    cs.set_clutch(False)
    assert cs.take()[0] is False


def test_leader_sender_keeps_sending_while_the_hud_thread_stalls(stack):
    """화면이 멈춰도 제어 패킷은 계속 나가야 한다 (워치독 오발동 방지)."""
    from home.client import CommandState, LeaderSender

    _, _, link, _ = stack
    leader = FakeLeaderArms()
    sender = LeaderSender(link=link, leader=leader, commands=CommandState(), rate_hz=60.0)
    sender.start()
    try:
        assert wait_until(lambda: _telemetry_state(link) is State.ALIGNING)
        # HUD 스레드가 700ms 멈춘 상황을 흉내낸다 (창 드래그 등).
        # 워치독 200ms 의 3배를 넘지만 송신 스레드가 살아있으므로 HOLD 로 가면 안 된다.
        # 0.5초는 send_hz 측정 창이 최소 한 번 닫히는 데도 필요하다.
        time.sleep(0.7)
        assert _telemetry_state(link) is State.ALIGNING, "제어 스레드가 살아있으면 HOLD 로 가면 안 된다"
        assert sender.send_hz > 45.0
    finally:
        sender.stop()


# --- 실물에서만 드러난 버그: 시리얼 포트 동시 접근 --------------------------


class ConcurrencyDetectingLeader:
    """읽기가 겹치면 실물 시리얼 포트처럼 실패하는 가짜 리더.

    lerobot 의 MotorsBus 는 스레드 안전하지 않아, 두 스레드가 동시에 읽으면
    "[TxRxResult] Port is in use!" 로 죽는다. FakeLeaderArms 는 순수 계산이라
    이 버그를 잡지 못했다.
    """

    def __init__(self):
        self._busy = False
        self._guard = __import__("threading").Lock()
        self.violations = 0

    def read_positions(self):
        with self._guard:
            if self._busy:
                self.violations += 1
                raise RuntimeError("Port is in use!")
            self._busy = True
        try:
            time.sleep(0.003)  # 시리얼 왕복 시간을 흉내낸다
            return [0.0] * N_JOINTS
        finally:
            with self._guard:
                self._busy = False

    def close(self):
        pass


def test_the_fake_detects_overlapping_reads():
    """위 가짜가 실제로 겹침을 잡아내는지 (아래 테스트의 의미를 보장한다)."""
    import threading

    leader = ConcurrencyDetectingLeader()
    errors = []

    def hammer():
        for _ in range(30):
            try:
                leader.read_positions()
            except RuntimeError as exc:
                errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert leader.violations > 0, "가짜가 겹침을 못 잡으면 이 테스트는 무의미하다"


def test_only_the_sender_thread_touches_the_leader(stack):
    """화면 표시용 값은 송신 스레드가 읽은 것을 재사용해야 한다.

    HUD 가 따로 read_positions() 를 부르면 실물에서 포트 충돌로 죽는다.
    """
    from home.client import CommandState, LeaderSender

    _, _, link, _ = stack
    leader = ConcurrencyDetectingLeader()
    sender = LeaderSender(link=link, leader=leader, commands=CommandState(), rate_hz=60.0)
    sender.start()
    try:
        # 송신 스레드가 읽은 값을 공개하므로 HUD 는 장치를 만질 필요가 없다
        assert wait_until(lambda: sender.last_joints is not None)
        # HUD 루프가 도는 것처럼 캐시를 반복 조회한다
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            joints = sender.last_joints
            assert joints is not None and len(joints) == N_JOINTS
            time.sleep(0.005)
        assert leader.violations == 0, "리더를 동시에 읽은 스레드가 있다"
    finally:
        sender.stop()
