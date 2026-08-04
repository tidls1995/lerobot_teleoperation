import socket
import time

import pytest

from common.protocol import (
    N_JOINTS,
    Cmd,
    ControlPacket,
    Flag,
    State,
    TelemetryPacket,
)
from mock.fake_arms import FakeFollowerArms
from tests.test_safety_states import make_config
from workbench.server import TeleopServer

ZEROS = tuple([0.0] * N_JOINTS)


class Client:
    """테스트용 UDP 제어 클라이언트."""

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2.0)
        self.addr = ("127.0.0.1", port)
        self.seq = 0

    def send(self, joints=ZEROS, clutch=False, cmd=Cmd.NONE):
        self.seq += 1
        pkt = ControlPacket(
            seq=self.seq, t_send=time.time(), clutch=clutch, cmd=cmd, joints=tuple(joints)
        )
        self.sock.sendto(pkt.pack(), self.addr)
        return self.seq

    def recv(self):
        data, _ = self.sock.recvfrom(4096)
        return TelemetryPacket.unpack(data)

    def exchange(self, **kwargs):
        seq = self.send(**kwargs)
        telem = self.recv()
        assert telem is not None
        assert telem.seq_echo == seq
        return telem

    def close(self):
        self.sock.close()


@pytest.fixture
def server_and_arms():
    from common.config import WorkbenchConfig

    arms = FakeFollowerArms()
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    yield server, arms
    server.stop()


def test_echoes_sequence_and_reports_state(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        telem = client.exchange()
        assert telem.state is State.ALIGNING
    finally:
        client.close()


def test_ignores_packets_with_bad_magic(server_and_arms):
    server, _ = server_and_arms
    raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw.settimeout(0.5)
    try:
        bad = bytearray(ControlPacket(1, 1.0, False, Cmd.NONE, ZEROS).pack())
        bad[0:4] = b"XXXX"
        raw.sendto(bytes(bad), ("127.0.0.1", server.control_port))
        with pytest.raises(socket.timeout):
            raw.recvfrom(4096)
    finally:
        raw.close()


def test_turns_torque_on_when_a_client_appears(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        assert arms.torque is True
    finally:
        client.close()


def test_full_engage_flow_drives_the_arms(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()  # ALIGNING
        telem = client.exchange(clutch=True)  # 정렬됨(둘 다 0) + 상승 에지
        assert telem.state is State.ENGAGED

        target = [5.0] * N_JOINTS
        for _ in range(10):
            telem = client.exchange(joints=target, clutch=True)
        # 속도 클램프 1.5도/프레임이므로 5도에 도달했어야 한다
        assert telem.joints == pytest.approx(target, abs=0.2)
    finally:
        client.close()


def test_speed_clamp_flag_reaches_the_client(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        telem = client.exchange(joints=[60.0] * N_JOINTS, clutch=True)
        assert telem.flags & Flag.SPEED_CLAMPED
    finally:
        client.close()


def test_watchdog_holds_when_client_goes_silent(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        time.sleep(0.4)  # watchdog_ms=200
        telem = client.exchange(clutch=True)
        assert telem.state is State.HOLD
        assert telem.flags & Flag.WATCHDOG
    finally:
        client.close()


def test_reset_recovers_from_hold(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        time.sleep(0.4)
        assert client.exchange().state is State.HOLD
        telem = client.exchange(cmd=Cmd.RESET)
        assert telem.state is State.ALIGNING
    finally:
        client.close()


def test_out_of_order_packet_is_ignored(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.seq = 1000
        client.exchange()
        # 낡은 시퀀스로 보내면 응답이 오지 않아야 한다
        stale = ControlPacket(seq=5, t_send=time.time(), clutch=False, cmd=Cmd.NONE, joints=ZEROS)
        client.sock.sendto(stale.pack(), client.addr)
        with pytest.raises(socket.timeout):
            client.sock.recvfrom(4096)
    finally:
        client.close()


def test_stop_disables_torque(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    client.exchange()
    client.close()
    server.stop()
    assert arms.torque is False


class FlakyArms(FakeFollowerArms):
    """지정한 호출 횟수 이후 서보 통신이 실패하는 팔 (스펙 §9)."""

    def __init__(self, fail_after: int):
        super().__init__()
        self._reads = 0
        self._fail_after = fail_after

    def read_positions(self):
        self._reads += 1
        if self._reads > self._fail_after:
            raise OSError("serial bus read failed")
        return super().read_positions()


def test_motor_failure_holds_after_three_retries():
    from common.config import WorkbenchConfig

    arms = FlakyArms(fail_after=5)
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    client = Client(server.control_port)
    try:
        deadline = time.monotonic() + 3.0
        telem = None
        while time.monotonic() < deadline:
            client.send()
            try:
                telem = client.recv()
            except socket.timeout:
                continue
            if telem is not None and telem.state is State.HOLD:
                break
        assert telem is not None
        assert telem.state is State.HOLD
        assert telem.flags & Flag.MOTOR_ERROR
    finally:
        client.close()
        server.stop()


def test_second_server_cannot_steal_the_control_port():
    """같은 포트에 두 번째 서버가 붙으면 조용히 성공해서는 안 된다.

    Windows 의 SO_REUSEADDR 은 이미 쓰는 UDP 포트에 다른 프로세스가 함께
    바인드하도록 허용하고, 도착한 데이터그램이 어느 소켓으로 갈지는 정해지지
    않는다. 그러면 조종자가 모르는 서버가 제어 명령을 받아가고, 진짜 서버는
    패킷이 비어 보여 워치독이 터진다. 두 번째 기동은 반드시 실패해야 한다.
    """
    from common.config import WorkbenchConfig

    cfg_a = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    first = TeleopServer(cfg=cfg_a, follower=FakeFollowerArms(), video=None)
    first.start()
    try:
        cfg_b = WorkbenchConfig(
            use_mock=True,
            control_port=first.control_port,
            video_port=0,
            cameras=[],
            safety=make_config(),
        )
        second = TeleopServer(cfg=cfg_b, follower=FakeFollowerArms(), video=None)
        with pytest.raises(OSError):
            second.start()
    finally:
        first.stop()


# --- 클라이언트 세션 ------------------------------------------------------------
#
# 순번만 보고 낡은 패킷을 버리면, 클라이언트가 재시작해 순번이 1부터 다시 시작할 때
# 전부 버리게 된다. 하루를 돌린 뒤라면 서버의 마지막 순번이 500만이 넘어 재시작한
# 클라이언트는 하루 동안 무시당한다 - 조종자 화면은 DISCONNECTED 로 굳고 되살리려면
# 누군가 작업대까지 걸어가야 한다.
#
# 실측(2026-08-04): 소킹 스모크 테스트에서 텔레메트리를 한 개도 못 받았다.

from workbench.server import ClientSession

A = ("192.168.0.9", 51000)
B = ("192.168.0.9", 51001)  # 같은 PC, 재시작해서 포트가 바뀐 클라이언트
C = ("192.168.0.77", 40000)  # 다른 사람


def make_session(takeover_after_s=0.2):
    return ClientSession(takeover_after_s=takeover_after_s)


def test_the_first_packet_starts_a_session():
    s = make_session()
    assert s.accept(A, seq=1, now=0.0) is True
    assert s.addr == A


def test_an_out_of_order_packet_is_dropped():
    """UDP 는 순서를 지키지 않는다. 늦게 온 낡은 패킷을 따르면 팔이 과거로 튄다."""
    s = make_session()
    s.accept(A, seq=10, now=0.0)
    assert s.accept(A, seq=9, now=0.01) is False
    assert s.accept(A, seq=11, now=0.02) is True


def test_a_restarted_client_is_picked_up_instead_of_ignored_forever():
    """이 프로젝트에서 실제로 겪은 고장이다."""
    s = make_session()
    s.accept(A, seq=5_000_000, now=0.0)  # 하루를 돌린 뒤

    # 클라이언트가 죽었다가 다시 떠서 1번부터 보낸다. 포트도 새로 잡힌다.
    assert s.accept(B, seq=1, now=5.0) is True
    assert s.addr == B
    assert s.accept(B, seq=2, now=5.02) is True


def test_a_client_restarting_on_the_same_port_is_also_picked_up():
    s = make_session()
    s.accept(A, seq=900_000, now=0.0)
    assert s.accept(A, seq=1, now=5.0) is True


def test_a_second_client_cannot_barge_in_while_the_first_is_talking():
    """두 사람이 동시에 보내면 팔이 두 명령 사이에서 튄다. 무시당하는 쪽이 안전하다."""
    s = make_session()
    s.accept(A, seq=1, now=0.0)
    assert s.accept(C, seq=1, now=0.05) is False
    assert s.addr == A
    assert s.accept(A, seq=2, now=0.06) is True


def test_takeover_needs_silence_longer_than_the_watchdog():
    s = make_session(takeover_after_s=0.2)
    s.accept(A, seq=100, now=0.0)
    assert s.accept(C, seq=1, now=0.19) is False, "워치독 전에는 넘겨주지 않는다"
    assert s.accept(C, seq=1, now=0.21) is True


def test_a_rejected_packet_does_not_move_the_session_forward():
    """거절한 패킷이 순번이나 시각을 갱신하면 정상 클라이언트가 밀려난다."""
    s = make_session()
    s.accept(A, seq=10, now=0.0)
    s.accept(C, seq=999, now=0.05)  # 거절됨
    assert s.addr == A
    assert s.accept(A, seq=11, now=0.06) is True, "A 의 다음 패킷은 여전히 받아야 한다"
