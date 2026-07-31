"""집 클라이언트 - 리더 읽기, 제어 송신, 텔레메트리 수신, 화면 조립.

RTT 는 seq 반향으로 잰다. 두 시각 모두 이쪽 시계에서 재므로 서버와 시계가
어긋나 있어도 정확하다 (스펙 §4.8). t_send 로 편도 지연을 계산하면 틀린다.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from typing import Callable

from common.config import HomeConfig, load_home_config
from common.protocol import (
    N_JOINTS,
    TELEMETRY_SIZE,
    Cmd,
    ControlPacket,
    TelemetryPacket,
    is_newer,
)

log = logging.getLogger(__name__)

_RECV_TIMEOUT = 0.05

#: RTT 계산용 송신 시각 보관 개수. 60Hz 에서 약 4초분.
_SENT_HISTORY = 256

#: 송신 스레드가 이보다 오래 멈추면 경고를 남긴다. 서버 워치독(200ms)보다
#: 낮게 잡아, 실제로 HOLD 가 걸리기 전에 조짐이 로그에 남게 한다.
_STALL_WARN_MS = 100.0


class ControlLink:
    """UDP 제어 채널. HUD 없이도 동작하므로 통합 테스트에서 그대로 쓸 수 있다."""

    def __init__(self, host: str, port: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._addr = (host, port)
        self._clock = clock
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._seq = 0
        self._sent_at: dict[int, float] = {}
        self._telemetry: tuple[TelemetryPacket, float] | None = None
        self._rtt_ms: float | None = None
        self._last_echo: int | None = None
        self._lost = 0

    @property
    def rtt_ms(self) -> float | None:
        with self._lock:
            return self._rtt_ms

    @property
    def lost_packets(self) -> int:
        with self._lock:
            return self._lost

    def latest_telemetry(self) -> tuple[TelemetryPacket, float] | None:
        """(패킷, 수신 시각(monotonic)) 또는 아직 없으면 None."""
        with self._lock:
            return self._telemetry

    def start(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(_RECV_TIMEOUT)
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._recv_loop, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send(self, joints, clutch: bool, reset: bool) -> None:
        if self._sock is None:
            raise RuntimeError("ControlLink.start() must be called first")
        if len(joints) != N_JOINTS:
            raise ValueError(f"joints must have {N_JOINTS} elements, got {len(joints)}")

        with self._lock:
            self._seq += 1
            seq = self._seq
            self._sent_at[seq] = self._clock()
            if len(self._sent_at) > _SENT_HISTORY:
                for old in sorted(self._sent_at)[: len(self._sent_at) - _SENT_HISTORY]:
                    del self._sent_at[old]

        packet = ControlPacket(
            seq=seq,
            t_send=time.time(),
            clutch=clutch,
            cmd=Cmd.RESET if reset else Cmd.NONE,
            joints=tuple(float(v) for v in joints),
        )
        try:
            self._sock.sendto(packet.pack(), self._addr)
        except OSError as exc:
            log.debug("control send failed: %s", exc)

    def _recv_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) != TELEMETRY_SIZE:
                continue
            telemetry = TelemetryPacket.unpack(data)
            if telemetry is None:
                continue

            now = self._clock()
            with self._lock:
                sent_at = self._sent_at.pop(telemetry.seq_echo, None)
                if sent_at is not None:
                    self._rtt_ms = (now - sent_at) * 1000.0
                if self._last_echo is not None and is_newer(telemetry.seq_echo, self._last_echo):
                    self._lost += telemetry.seq_echo - self._last_echo - 1
                self._last_echo = telemetry.seq_echo
                self._telemetry = (telemetry, now)


class CommandState:
    """HUD 스레드가 쓰고 송신 스레드가 읽는 '최신 조종 입력'.

    제어 송신을 화면 그리기와 **다른 스레드로 분리**하기 위한 통로다.
    한 루프에 합치면 창을 드래그하는 것만으로도 pygame 이 수백 ms 멈추고,
    그 사이 제어 패킷이 끊겨 서버 워치독이 터진다. 화면이 렉 걸린다고
    로봇이 멈춰서는 안 된다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clutch = False
        self._reset_pending = False

    def set_clutch(self, value: bool) -> None:
        with self._lock:
            self._clutch = value

    def request_reset(self) -> None:
        with self._lock:
            self._reset_pending = True

    def take(self) -> tuple[bool, bool]:
        """(clutch, reset) 을 읽는다. reset 은 정확히 한 번만 실린다."""
        with self._lock:
            reset, self._reset_pending = self._reset_pending, False
            return self._clutch, reset


class LeaderSender:
    """리더를 읽어 60Hz 로 제어 패킷을 보내는 전용 스레드."""

    def __init__(
        self,
        link: ControlLink,
        leader,
        commands: CommandState,
        rate_hz: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        self._link = link
        self._leader = leader
        self._commands = commands
        self._interval = 1.0 / rate_hz
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._rate_lock = threading.Lock()
        self._sent = 0
        self._rate_since = 0.0
        self._send_hz = 0.0

    @property
    def send_hz(self) -> float:
        with self._rate_lock:
            return self._send_hz

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._rate_since = self._clock()
        self._thread = threading.Thread(target=self._loop, name="leader-send", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        next_t = self._clock()
        last_sent_at: float | None = None
        while not self._stop.is_set():
            clutch, reset = self._commands.take()
            now = self._clock()

            # 경계 계측: 이 스레드가 실제로 얼마 만에 다시 돌았는가.
            # 서버의 워치독 로그와 짝을 이룬다. 여기에 큰 값이 찍히면 클라이언트가
            # 멈춘 것이고, 여기는 멀쩡한데 서버가 못 받았다면 회선 쪽 문제다.
            if last_sent_at is not None:
                gap_ms = (now - last_sent_at) * 1000.0
                if gap_ms > _STALL_WARN_MS:
                    log.warning(
                        "control send stalled for %.0f ms (target %.1f ms) - "
                        "the sender thread was blocked, not the network",
                        gap_ms,
                        self._interval * 1000.0,
                    )
            last_sent_at = now

            try:
                self._link.send(joints=self._leader.read_positions(), clutch=clutch, reset=reset)
            except Exception:
                log.exception("leader send failed")
            self._tick_rate()

            next_t += self._interval
            remaining = next_t - self._clock()
            if remaining > 0:
                self._stop.wait(remaining)
            else:
                # 밀렸으면 몰아서 따라잡지 않는다. 밀린 만큼은 그냥 건너뛴다.
                next_t = self._clock()

    def _tick_rate(self) -> None:
        now = self._clock()
        with self._rate_lock:
            self._sent += 1
            elapsed = now - self._rate_since
            if elapsed >= 0.5:
                self._send_hz = self._sent / elapsed
                self._sent = 0
                self._rate_since = now


def build_leader(cfg: HomeConfig):
    """설정에 따라 mock/실물 리더를 만든다. 하드웨어는 아직 만지지 않는다."""
    if cfg.use_mock:
        from mock.fake_arms import FakeLeaderArms

        return FakeLeaderArms()

    from home.leader_arms import RealLeaderArms

    if not cfg.arms:
        raise ValueError(
            "use_mock is false but the config has no 'arms' section; "
            "add serial numbers for the leader arms"
        )
    return RealLeaderArms(arms=cfg.arms)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SO-101 teleoperation home client")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument(
        "--cameras",
        type=int,
        default=3,
        help="how many camera panes to show (0 for the no-camera bring-up)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg: HomeConfig = load_home_config(args.config)

    from home.hud import Hud, HudStats
    from home.video_recv import VideoClient

    leader = build_leader(cfg)
    connect = getattr(leader, "connect", None)
    if callable(connect):
        connect()

    link = ControlLink(host=cfg.server_host, port=cfg.control_port)
    video = VideoClient(host=cfg.server_host, port=cfg.video_port)
    cam_ids = list(range(args.cameras))
    hud = Hud(cam_ids=cam_ids, cam_names={0: "front", 1: "wrist_left", 2: "wrist_right"})

    commands = CommandState()
    sender = LeaderSender(link=link, leader=leader, commands=commands)

    link.start()
    video.start()
    sender.start()

    try:
        while True:
            now = time.monotonic()
            action = hud.poll(now)
            if action.quit:
                break
            # M 키는 mock 리더 전용이다. 실물 리더에는 motion_enabled 가 없다.
            if action.toggle_motion and hasattr(leader, "motion_enabled"):
                leader.motion_enabled = not leader.motion_enabled
                log.info("mock leader motion: %s", leader.motion_enabled)

            # 제어 송신은 별도 스레드가 한다. 여기서는 최신 입력만 넘긴다.
            commands.set_clutch(action.clutch)
            if action.reset:
                commands.request_reset()

            leader_joints = leader.read_positions()  # 화면 표시용
            got = link.latest_telemetry()
            telemetry = got[0] if got else None
            age_ms = (now - got[1]) * 1000.0 if got else None

            frames = {}
            for cam_id in cam_ids:
                latest = video.latest(cam_id)
                frames[cam_id] = latest[0] if latest else None

            hud.draw(
                frames=frames,
                telemetry=telemetry,
                leader_joints=leader_joints,
                stats=HudStats(
                    rtt_ms=link.rtt_ms,
                    lost_packets=link.lost_packets,
                    video_connected=video.connected,
                    telemetry_age_ms=age_ms,
                    send_hz=sender.send_hz,
                ),
                align_threshold_deg=3.0,
                now=now,
            )
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        sender.stop()
        hud.close()
        video.stop()
        link.stop()
        leader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
