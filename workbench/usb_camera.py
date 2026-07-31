"""USB 웹캠을 Camera Protocol 로 감싼다.

read() 는 실패해도 예외를 던지지 않고 None 을 돌려준다. 카메라 1대가 죽었다고
전체 조종이 멈추면 안 되고, CameraPublisher 가 그 프레임만 건너뛰면 되기
때문이다 (스펙 §9).
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)


class CameraOpenError(Exception):
    """카메라를 열 수 없다."""


class UsbCamera:
    def __init__(self, cam_id: int, name: str, index: int, width: int, height: int, fps: int) -> None:
        self._cam_id = cam_id
        self._name = name
        self._index = index
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._actual_size: tuple[int, int] | None = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def actual_size(self) -> tuple[int, int] | None:
        """장치가 실제로 준 해상도. 요청값과 다를 수 있다."""
        return self._actual_size

    def open(self) -> None:
        # Windows 에서는 DirectShow 백엔드가 기본(MSMF)보다 열기가 빠르고 안정적이다.
        cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise CameraOpenError(
                f"camera {self._cam_id} ({self._name}): cannot open device index {self._index}. "
                "Run 'python -m tools.probe_hardware --cameras' to see which indices exist."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        # 드라이버가 프레임을 쌓아두면 영상이 뒤처진다. 최신성을 지키는 쪽을
        # 택한다 (스펙 §5.6). 지원하지 않는 드라이버에서는 조용히 무시된다.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise CameraOpenError(
                f"camera {self._cam_id} ({self._name}): opened device index {self._index} "
                "but the first frame read failed"
            )

        self._cap = cap
        self._actual_size = (frame.shape[1], frame.shape[0])
        if self._actual_size != (self._width, self._height):
            log.warning(
                "camera %d (%s): asked for %dx%d, device gave %dx%d",
                self._cam_id,
                self._name,
                self._width,
                self._height,
                *self._actual_size,
            )

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            log.warning("camera %d (%s): frame read failed", self._cam_id, self._name)
            return None
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
