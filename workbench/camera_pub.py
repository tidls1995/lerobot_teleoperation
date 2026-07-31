"""카메라 캡처 -> JPEG 인코딩 -> TCP 송신.

핵심은 **1슬롯 버퍼**다. 퍼블리셔는 최신 프레임 1장만 들고 있고, 송신이
밀리는 동안 찍힌 프레임은 그냥 덮어써서 버린다. 큐에 쌓으면 영상이 점점
뒤처져 결국 수 초 전 화면을 보며 조종하게 된다. 화질이 아니라 최신성을
지키는 쪽을 택한다 (스펙 §5.6).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import cv2

from common.devices import Camera
from common.protocol import VideoHeader

log = logging.getLogger(__name__)


class CameraPublisher:
    """카메라 1대를 지정한 fps 로 캡처해 JPEG 1장을 항상 최신으로 유지한다."""

    def __init__(
        self,
        camera: Camera,
        cam_id: int,
        fps: int,
        jpeg_quality: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self._camera = camera
        self._cam_id = cam_id
        self._interval = 1.0 / fps
        self._encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
        self._clock = clock

        self._lock = threading.Lock()
        self._latest: tuple[bytes, float, int] | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def cam_id(self) -> int:
        return self._cam_id

    def capture_once(self) -> None:
        """한 장 찍어 최신 슬롯에 넣는다. 테스트와 캡처 스레드가 함께 쓴다."""
        frame = self._camera.read()
        if frame is None:
            log.warning("camera %d: capture failed, skipping frame", self._cam_id)
            return
        ok, buf = cv2.imencode(".jpg", frame, self._encode_params)
        if not ok:
            log.warning("camera %d: jpeg encode failed, skipping frame", self._cam_id)
            return
        with self._lock:
            self._seq += 1
            self._latest = (buf.tobytes(), self._clock(), self._seq)

    def latest(self) -> tuple[bytes, float, int] | None:
        """(jpeg, t_capture, seq) 또는 아직 한 장도 없으면 None."""
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._thread is not None:
            return
        # 실물 카메라는 여기서 장치를 연다. mock 에는 open() 이 없다.
        opener = getattr(self._camera, "open", None)
        if callable(opener):
            try:
                opener()
            except Exception:
                # 카메라 1대 때문에 조종 전체를 못 하게 만들지 않는다 (스펙 §9).
                # 이 퍼블리셔는 스레드를 띄우지 않고, latest() 는 None 으로 남는다.
                # 클라이언트 화면에는 그 칸만 "no signal" 로 뜬다.
                log.exception("camera %d: open failed, this camera is disabled", self._cam_id)
                return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"cam{self._cam_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._camera.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            try:
                self.capture_once()
            except Exception:
                log.exception("camera %d: capture loop error", self._cam_id)
            elapsed = self._clock() - started
            self._stop.wait(max(0.0, self._interval - elapsed))


class VideoServer:
    """TCP 로 모든 카메라의 최신 프레임을 한 연결에 다중화해 내보낸다.

    한 번에 한 클라이언트만 받는다. 새 연결이 오면 기존 연결을 끊는데,
    재접속 시 유령 연결이 남아 대역폭을 갉아먹는 것을 막기 위함이다.
    """

    def __init__(
        self,
        port: int,
        publishers: list[CameraPublisher],
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.002,
    ) -> None:
        self._requested_port = port
        self._publishers = publishers
        self._clock = clock
        self._poll_interval = poll_interval

        self._listener: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._send_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = port

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR 을 걸지 않는다. Windows 에서는 다른 프로세스가 같은 포트를
        # 가로챌 수 있고, 죽지 않은 옛 서버가 영상을 대신 내보내면 조종자는
        # '움직이는 옛 화면'을 보게 되어 알아채기가 더 어렵다.
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("0.0.0.0", self._requested_port))
        except OSError as exc:
            listener.close()
            raise OSError(
                f"cannot bind video port TCP {self._requested_port}: {exc}. "
                "Another teleoperation server is probably still running - "
                "stop it before starting a new one."
            ) from exc
        listener.listen(1)
        listener.settimeout(0.5)
        self._listener = listener
        self.port = listener.getsockname()[1]

        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="video-accept", daemon=True)
        self._send_thread = threading.Thread(target=self._send_loop, name="video-send", daemon=True)
        self._accept_thread.start()
        self._send_thread.start()
        log.info("video server listening on port %d", self.port)

    def stop(self) -> None:
        self._stop.set()
        for t in (self._accept_thread, self._send_thread):
            if t is not None:
                t.join(timeout=2.0)
        self._accept_thread = None
        self._send_thread = None
        self._close_conn()
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            log.info("video client connected from %s", addr)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._close_conn()  # 새 연결이 기존 연결을 대체한다
            with self._conn_lock:
                self._conn = conn

    def _send_loop(self) -> None:
        last_sent: dict[int, int] = {}
        while not self._stop.is_set():
            with self._conn_lock:
                conn = self._conn
            if conn is None:
                self._stop.wait(0.05)
                continue

            sent_any = False
            for pub in self._publishers:
                latest = pub.latest()
                if latest is None:
                    continue
                jpeg, t_capture, seq = latest
                if last_sent.get(pub.cam_id) == seq:
                    continue  # 아직 새 프레임이 없다
                header = VideoHeader(cam_id=pub.cam_id, seq=seq, t_capture=t_capture, length=len(jpeg))
                try:
                    conn.sendall(header.pack() + jpeg)
                except OSError:
                    log.info("video client disconnected")
                    self._close_conn()
                    last_sent.clear()
                    break
                last_sent[pub.cam_id] = seq
                sent_any = True

            if not sent_any:
                self._stop.wait(self._poll_interval)

    def _close_conn(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
