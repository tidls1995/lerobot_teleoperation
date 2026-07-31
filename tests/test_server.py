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
