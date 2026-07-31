"""하드웨어 없이 개발·테스트하기 위한 팔 대역.

실물과 동일한 Protocol 을 구현하므로 서버·클라이언트는 차이를 모른다.
문제가 생겼을 때 mock 으로 갈아끼워 '네트워크 문제인가 하드웨어 문제인가'를
즉시 판별할 수 있다.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Sequence

from common.protocol import N_JOINTS


class FakeFollowerArms:
    """메모리 변수에 자세를 담아두는 가짜 팔로워.

    Args:
        initial: 시작 관절각. 기본 0도.
        lag: 0.0 이면 명령에 즉시 도달, 1.0 에 가까울수록 느리게 따라간다.
             실제 서보의 관성을 흉내내 추종 오차 로직을 시험하는 용도.
        blocks: {관절인덱스: 넘지 못하는 각도}. '팔이 뭔가에 걸림'을 흉내낸다.
    """

    def __init__(
        self,
        initial: Sequence[float] | None = None,
        lag: float = 0.0,
        blocks: dict[int, float] | None = None,
    ) -> None:
        if initial is not None and len(initial) != N_JOINTS:
            raise ValueError(f"initial must have {N_JOINTS} elements")
        if not 0.0 <= lag < 1.0:
            raise ValueError("lag must be in [0.0, 1.0)")
        self._actual = [float(v) for v in (initial if initial is not None else [0.0] * N_JOINTS)]
        self._lag = lag
        self._blocks = dict(blocks or {})
        self.torque = False

    def read_positions(self) -> list[float]:
        return list(self._actual)

    def write_positions(self, angles: Sequence[float]) -> None:
        if len(angles) != N_JOINTS:
            raise ValueError(f"angles must have {N_JOINTS} elements, got {len(angles)}")
        for i, commanded in enumerate(angles):
            target = float(commanded)
            if i in self._blocks:
                target = min(target, self._blocks[i])
            self._actual[i] += (target - self._actual[i]) * (1.0 - self._lag)

    def set_torque(self, enabled: bool) -> None:
        self.torque = bool(enabled)

    def close(self) -> None:
        pass


class FakeLeaderArms:
    """가짜 리더.

    ``motion_enabled`` 가 False 인 동안에는 ``base`` 자세를 그대로 낸다.
    mock 데모에서 리더와 팔로워를 같은 자세에서 출발시켜 정렬 절차를
    통과할 수 있게 하기 위함이다. True 로 켜면 관절마다 위상이 다른
    사인파를 그려 팔로워가 실제로 따라오는 것을 눈으로 확인할 수 있다.
    """

    def __init__(
        self,
        base: Sequence[float] | None = None,
        amplitude_deg: float = 20.0,
        period_s: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if base is not None and len(base) != N_JOINTS:
            raise ValueError(f"base must have {N_JOINTS} elements")
        if period_s <= 0:
            raise ValueError("period_s must be positive")
        self._base = [float(v) for v in (base if base is not None else [0.0] * N_JOINTS)]
        self._amplitude = amplitude_deg
        self._period = period_s
        self._clock = clock
        self.motion_enabled = False

    def read_positions(self) -> list[float]:
        if not self.motion_enabled:
            return list(self._base)
        t = self._clock()
        return [
            self._base[i] + self._amplitude * math.sin(2.0 * math.pi * (t / self._period + i / N_JOINTS))
            for i in range(N_JOINTS)
        ]

    def close(self) -> None:
        pass
