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
        sock = self._bind_control_socket()
        self._sock = sock
        self.control_port = sock.getsockname()[1]

        # 실물 어댑터는 여기서 시리얼 포트를 연다. mock 에는 connect() 가 없다.
        # 포트를 못 잡았으면 하드웨어를 건드리지 않고 죽는 편이 낫기 때문에
        # 바인드 뒤에 둔다.
        connect = getattr(self._follower, "connect", None)
        if callable(connect):
            connect()

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

    def _bind_control_socket(self) -> socket.socket:
        """제어 소켓을 **배타적으로** 연다.

        SO_REUSEADDR 을 걸지 않는다. Windows 에서 이 옵션은 '이미 쓰는 UDP 포트에
        함께 바인드해도 된다'는 뜻이고, 그러면 도착한 데이터그램이 어느 소켓으로
        갈지 정해지지 않는다. 실제로 이 때문에 죽지 않은 옛 서버가 제어 패킷을
        가로채고, 그 서버의 last_seq 가 높아 전부 '낡은 패킷'으로 폐기해서
        조종자 화면이 DISCONNECTED 로 남거나 워치독이 간헐적으로 터졌다.

        두 번째 기동은 조용히 성공하는 대신 **큰 소리로 실패해야 한다.**
        조종자가 모르는 서버가 로봇 명령을 받아가는 상황을 만들지 않기 위함이다.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Windows 전용. 다른 프로세스가 SO_REUSEADDR 로 이 포트를 가로채는 것도 막는다.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("0.0.0.0", self._cfg.control_port))
        except OSError as exc:
            sock.close()
            raise OSError(
                f"cannot bind control port UDP {self._cfg.control_port}: {exc}. "
                "Another teleoperation server is probably still running - "
                "stop it before starting a new one."
            ) from exc
        sock.settimeout(_RECV_TIMEOUT)
        return sock

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

    조립 시점에는 하드웨어를 만지지 않는다. 실제 시리얼 포트와 카메라를 여는
    것은 TeleopServer.start() 와 CameraPublisher.start() 다.
    """
    if cfg.use_mock:
        from mock.fake_arms import FakeFollowerArms
        from mock.fake_cameras import FakeCamera

        follower = FakeFollowerArms()
        cameras = [
            FakeCamera(cam_id=c.id, name=c.name, width=c.width, height=c.height) for c in cfg.cameras
        ]
    else:
        from workbench.follower_arms import RealFollowerArms
        from workbench.usb_camera import UsbCamera

        if not cfg.arms:
            raise ValueError(
                "use_mock is false but the config has no 'arms' section; "
                "add serial numbers for the follower arms"
            )
        follower = RealFollowerArms(arms=cfg.arms)
        cameras = [
            UsbCamera(
                cam_id=c.id, name=c.name, index=c.index, width=c.width, height=c.height, fps=c.fps
            )
            for c in cfg.cameras
        ]

    publishers = [
        CameraPublisher(camera=cam, cam_id=c.id, fps=c.fps, jpeg_quality=c.jpeg_quality)
        for cam, c in zip(cameras, cfg.cameras)
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
