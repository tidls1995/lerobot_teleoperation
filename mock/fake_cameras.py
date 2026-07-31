"""합성 영상 생성기.

프레임 번호와 시각을 큼직하게 그려 넣는다. 화면에 뜬 번호가 멈춰 있으면
영상이 끊긴 것이고, 번호가 건너뛰면 프레임이 버려진 것이다. 실제 카메라로는
알 수 없는 것들을 눈으로 확인할 수 있게 하는 것이 목적이다.
"""

from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

# 카메라마다 배경색을 다르게 해서 화면에서 즉시 구분되게 한다 (BGR).
_BACKGROUNDS = [(60, 40, 40), (40, 60, 40), (40, 40, 60)]


class FakeCamera:
    def __init__(
        self,
        cam_id: int,
        name: str,
        width: int,
        height: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cam_id = cam_id
        self._name = name
        self._width = width
        self._height = height
        self._clock = clock
        self._frame_number = 0

    @property
    def frame_number(self) -> int:
        return self._frame_number

    def read(self) -> np.ndarray:
        self._frame_number += 1
        t = self._clock()

        frame = np.empty((self._height, self._width, 3), dtype=np.uint8)
        frame[:, :] = _BACKGROUNDS[self._cam_id % len(_BACKGROUNDS)]

        # 시각에 따라 도는 막대 - 영상이 살아있는지 한눈에 보인다
        cx, cy = self._width // 2, self._height // 2
        radius = min(cx, cy) - 6
        angle = 2.0 * np.pi * (t % 4.0) / 4.0
        end = (int(cx + radius * np.cos(angle)), int(cy + radius * np.sin(angle)))
        cv2.line(frame, (cx, cy), end, (200, 200, 200), 2)
        cv2.circle(frame, (cx, cy), radius, (90, 90, 90), 1)

        scale = self._width / 320.0
        cv2.putText(
            frame,
            self._name,
            (6, int(18 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5 * scale,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"#{self._frame_number}",
            (6, self._height - int(8 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5 * scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"{t:8.2f}s",
            (self._width - int(110 * scale), self._height - int(8 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45 * scale,
            (180, 220, 180),
            1,
            cv2.LINE_AA,
        )
        return frame

    def close(self) -> None:
        pass
