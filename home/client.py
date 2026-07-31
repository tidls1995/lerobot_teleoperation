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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SO-101 teleoperation home client")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg: HomeConfig = load_home_config(args.config)

    if not cfg.use_mock:
        raise NotImplementedError(
            "real leader arms land in stage 2; set use_mock: true in the config"
        )

    from home.hud import Hud, HudStats
    from home.video_recv import VideoClient
    from mock.fake_arms import FakeLeaderArms

    leader = FakeLeaderArms()
    link = ControlLink(host=cfg.server_host, port=cfg.control_port)
    video = VideoClient(host=cfg.server_host, port=cfg.video_port)
    cam_ids = [0, 1, 2]
    hud = Hud(cam_ids=cam_ids, cam_names={0: "front", 1: "wrist_left", 2: "wrist_right"})

    link.start()
    video.start()

    send_interval = 1.0 / 60.0
    next_send = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            action = hud.poll(now)
            if action.quit:
                break
            if action.toggle_motion:
                leader.motion_enabled = not leader.motion_enabled
                log.info("mock leader motion: %s", leader.motion_enabled)

            leader_joints = leader.read_positions()
            if now >= next_send:
                link.send(joints=leader_joints, clutch=action.clutch, reset=action.reset)
                next_send = now + send_interval

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
                ),
                align_threshold_deg=3.0,
                now=now,
            )
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        hud.close()
        video.stop()
        link.stop()
        leader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
