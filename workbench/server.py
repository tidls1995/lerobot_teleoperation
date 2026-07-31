"""작업대 서버 - 제어 루프와 조립.

제어 채널은 UDP 다. 낡은 관절각을 재전송받아봐야 쓸모가 없기 때문이다
(스펙 §3). 영상은 별도 TCP 연결로 나가므로 영상 혼잡이 제어를 밀어내지 않는다.

와이어에 싣는 시각은 time.time(), 내부 판정은 time.monotonic() 을 쓴다.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from typing import Callable

from common.config import WorkbenchConfig, load_workbench_config
from common.devices import FollowerArms
from common.protocol import (
    CONTROL_SIZE,
    N_JOINTS,
    ControlPacket,
    Flag,
    State,
    TelemetryPacket,
    is_newer,
)
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.safety import SafetyGate

log = logging.getLogger(__name__)

#: 제어 소켓 수신 타임아웃. 패킷이 없어도 이 주기로 워치독을 돌린다.
_RECV_TIMEOUT = 0.005

#: 서보 읽기를 몇 번까지 즉시 재시도할 것인가. 초과하면 통신 고장으로 본다.
_MOTOR_RETRIES = 3

_MOTOR_FAILURE_REASON = "motor communication failure"


class TeleopServer:
    def __init__(
        self,
        cfg: WorkbenchConfig,
        follower: FollowerArms,
        video: VideoServer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._follower = follower
        self._video = video
        self._clock = clock
        self._gate = SafetyGate(cfg.safety)

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._torque_state: bool | None = None
        self._last_actual: list[float] = [0.0] * N_JOINTS
        self.control_port = cfg.control_port

    @property
    def state(self) -> State:
        return self._gate.state

    @property
    def video_port(self) -> int | None:
        return self._video.port if self._video is not None else None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self._cfg.control_port))
        sock.settimeout(_RECV_TIMEOUT)
        self._sock = sock
        self.control_port = sock.getsockname()[1]

        if self._video is not None:
            self._video.start()

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="control", daemon=True)
        self._thread.start()
        log.info("control server listening on UDP %d", self.control_port)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._video is not None:
            self._video.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            self._follower.set_torque(False)
        finally:
            self._follower.close()

    def _loop(self) -> None:
        assert self._sock is not None
        last_seq: int | None = None
        client_addr: tuple[str, int] | None = None

        while not self._stop.is_set():
            packet: ControlPacket | None = None
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                pass
            except OSError:
                break
            else:
                if len(data) == CONTROL_SIZE:
                    candidate = ControlPacket.unpack(data)
                    if candidate is None:
                        log.debug("rejected packet from %s (bad magic)", addr)
                    elif last_seq is not None and not is_newer(candidate.seq, last_seq):
                        log.debug("dropped stale packet seq=%d (last=%d)", candidate.seq, last_seq)
                    else:
                        last_seq = candidate.seq
                        packet = candidate
                        client_addr = addr
                else:
                    log.debug("rejected packet from %s (size %d)", addr, len(data))

            now = self._clock()
            actual, motor_failed = self._read_actual()
            extra_flags = 0
            if motor_failed:
                extra_flags |= Flag.MOTOR_ERROR
                self._gate.force_hold(_MOTOR_FAILURE_REASON)

            result = self._gate.step(packet, actual, now)

            if self._torque_state != result.torque:
                try:
                    self._follower.set_torque(result.torque)
                    self._torque_state = result.torque
                except Exception:
                    log.exception("follower set_torque failed")
                    extra_flags |= Flag.MOTOR_ERROR
                    self._gate.force_hold(_MOTOR_FAILURE_REASON)

            if result.targets is not None and not motor_failed:
                try:
                    self._follower.write_positions(result.targets)
                except Exception:
                    log.exception("follower write failed")
                    extra_flags |= Flag.MOTOR_ERROR
                    self._gate.force_hold(_MOTOR_FAILURE_REASON)

            if packet is not None and client_addr is not None:
                telemetry = TelemetryPacket(
                    seq_echo=packet.seq,
                    t_send=time.time(),
                    state=self._gate.state,
                    flags=result.flags | extra_flags,
                    joints=tuple(actual),
                )
                try:
                    self._sock.sendto(telemetry.pack(), client_addr)
                except OSError:
                    log.debug("telemetry send failed")

    def _read_actual(self) -> tuple[list[float], bool]:
        """팔로워의 실제각을 읽는다.

        서보 버스는 가끔 한 번씩 읽기에 실패한다. 순간적인 실패로 HOLD 를 걸면
        쓸 수 없으므로 즉시 재시도하고, 연속 3회 실패해야 진짜 고장으로 본다
        (스펙 §9).

        Returns:
            (관절각, 통신 실패 여부). 실패했으면 마지막으로 성공한 값을 돌려준다.
        """
        for _ in range(_MOTOR_RETRIES):
            try:
                actual = self._follower.read_positions()
            except Exception as exc:
                log.warning("follower read failed: %s", exc)
                continue
            self._last_actual = actual
            return actual, False
        return list(self._last_actual), True


def build_server(cfg: WorkbenchConfig) -> tuple[TeleopServer, list[CameraPublisher]]:
    """설정에 따라 mock/실물을 조립한다.

    1단계에서는 use_mock 이 반드시 true 여야 한다. 실물 어댑터는 2단계에서
    추가된다.
    """
    if not cfg.use_mock:
        raise NotImplementedError(
            "real hardware adapters land in stage 2; set use_mock: true in the config"
        )

    from mock.fake_arms import FakeFollowerArms
    from mock.fake_cameras import FakeCamera

    follower = FakeFollowerArms()
    publishers = [
        CameraPublisher(
            camera=FakeCamera(cam_id=c.id, name=c.name, width=c.width, height=c.height),
            cam_id=c.id,
            fps=c.fps,
            jpeg_quality=c.jpeg_quality,
        )
        for c in cfg.cameras
    ]
    video = VideoServer(port=cfg.video_port, publishers=publishers)
    return TeleopServer(cfg=cfg, follower=follower, video=video), publishers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SO-101 teleoperation workbench server")
    parser.add_argument("--config", default="config/workbench.yaml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = load_workbench_config(args.config)
    server, publishers = build_server(cfg)
    for pub in publishers:
        pub.start()
    server.start()

    print(f"control  UDP  {server.control_port}")
    print(f"video    TCP  {server.video_port}")
    print("Ctrl-C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
        for pub in publishers:
            pub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
