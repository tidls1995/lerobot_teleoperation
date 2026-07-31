"""하드웨어 경계.

여기 있는 Protocol 들이 '실물'과 'mock'이 만나는 유일한 접점이다.
1단계에서는 mock 만 구현하고, 2단계에서 lerobot 기반 실물 어댑터가 같은
Protocol 을 구현한다. 서버·클라이언트 코드는 어느 쪽인지 알 필요가 없다.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

import numpy as np


@runtime_checkable
class FollowerArms(Protocol):
    """작업대의 팔로워 암 2대 (관절 12개)를 하나로 묶은 인터페이스."""

    def read_positions(self) -> list[float]:
        """현재 **실제** 관절각(도) 12개."""

    def write_positions(self, angles: Sequence[float]) -> None:
        """목표 관절각(도) 12개를 명령한다."""

    def set_torque(self, enabled: bool) -> None:
        """토크를 켜고 끈다. 끄면 팔이 손으로 움직여진다."""

    def close(self) -> None:
        """장치를 정리한다."""


@runtime_checkable
class LeaderArms(Protocol):
    """집의 리더 암 2대. 읽기 전용 (토크가 꺼져 있다)."""

    def read_positions(self) -> list[float]:
        """현재 관절각(도) 12개."""

    def close(self) -> None:
        """장치를 정리한다."""


@runtime_checkable
class Camera(Protocol):
    def read(self) -> np.ndarray | None:
        """BGR 프레임 한 장. 실패하면 None."""

    def close(self) -> None:
        """장치를 정리한다."""
