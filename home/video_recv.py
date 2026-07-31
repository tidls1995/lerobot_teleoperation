"""영상 수신 - TCP 연결, 프레이밍 해제, JPEG 디코딩, 자동 재접속.

연결은 **집에서 개시한다.** 데이터는 작업대 -> 집 단방향으로 흐르지만
포트포워딩이 작업대에만 설정되어 있기 때문이다 (스펙 §4.5).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import cv2
import numpy as np

from common.netutil import recv_exactly
from common.protocol import VIDEO_HEADER_SIZE, VideoHeader

log = logging.getLogger(__name__)

#: 말이 안 되는 길이의 헤더를 받으면 스트림이 어긋난 것으로 본다.
_MAX_FRAME_BYTES = 8 * 1024 * 1024


class VideoClient:
    def __init__(
        self,
        host: str,
        port: int,
        reconnect_delay: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self._port = port
        self._reconnect_delay = reconnect_delay
        self._clock = clock

        self._lock = threading.Lock()
        self._frames: dict[int, tuple[np.ndarray, float, int]] = {}
        self._connected = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def latest(self, cam_id: int) -> tuple[np.ndarray, float, int] | None:
        """(frame, t_capture, seq) 또는 아직 받은 게 없으면 None."""
        with self._lock:
            return self._frames.get(cam_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="video-recv", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._connected = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection((self._host, self._port), timeout=3.0) as sock:
                    sock.settimeout(2.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._lock:
                        self._connected = True
                    log.info("video connected to %s:%d", self._host, self._port)
                    self._receive_forever(sock)
            except OSError as exc:
                log.debug("video connection failed: %s", exc)
            finally:
                with self._lock:
                    self._connected = False
            self._stop.wait(self._reconnect_delay)

    def _receive_forever(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
            except socket.timeout:
                continue
            if header_bytes is None:
                return  # 연결 종료

            header = VideoHeader.unpack(header_bytes)
            if header is None:
                log.warning("video: bad frame header, dropping connection")
                return  # 스트림이 어긋났다. 재접속이 유일한 복구 방법이다.
            if not (0 < header.length <= _MAX_FRAME_BYTES):
                log.warning("video: implausible frame length %d, dropping connection", header.length)
                return

            payload = recv_exactly(sock, header.length)
            if payload is None:
                return

            frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                log.warning("video: jpeg decode failed for cam %d", header.cam_id)
                continue

            with self._lock:
                self._frames[header.cam_id] = (frame, header.t_capture, header.seq)
