"""실물 팔로워 암 2대. lerobot 의 SOFollower 를 FollowerArms Protocol 로 감싼다.

1단계에서 만든 서버는 이 Protocol 만 알고 있으므로, 설정 한 줄로 mock 과
갈아끼울 수 있다. 문제가 생겼을 때 mock 으로 돌려 '네트워크냐 하드웨어냐'를
즉시 가릴 수 있는 것이 이 구조의 목적이다.
"""

from __future__ import annotations

import logging
from typing import Sequence

from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

from common.config import ArmConfig
from common.joints import ARM_SIDES, require_both_sides, to_arrays, to_dicts
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)


class RealFollowerArms:
    def __init__(self, arms: dict[str, ArmConfig]) -> None:
        require_both_sides(arms)
        self._arms = arms
        self._buses: dict[str, SOFollower] = {}

    @property
    def is_connected(self) -> bool:
        return bool(self._buses)

    def connect(self) -> None:
        """시리얼 포트를 열고 캘리브레이션을 모터에 쓴다.

        calibrate=False 로 연결한다. lerobot 의 calibrate() 는 input() 으로 사람에게
        팔을 움직이라고 요구하는 대화형 절차이므로 서버 안에서 돌면 조종 루프가
        멈춘다. 캘리브레이션 파일은 lerobot-calibrate CLI 로 미리 만들어 둔다
        (스펙 §7.2).
        """
        for side in ARM_SIDES:
            arm = self._arms[side]
            port = resolve_port_spec(arm.serial_number, arm.port)
            log.info("follower %s: opening %s (calibration id %s)", side, port, arm.calibration_id)
            robot = SOFollower(
                SOFollowerRobotConfig(
                    port=port,
                    id=arm.calibration_id,
                    use_degrees=True,
                    # 클램프의 단일 출처는 safety.py 다. 여기서도 걸면 안전 로직이
                    # 두 곳으로 흩어지고, send_action 마다 Present_Position 을 다시
                    # 읽어 제어 주기가 떨어진다 (스펙 §5.4).
                    max_relative_target=None,
                    disable_torque_on_disconnect=True,
                    cameras={},
                )
            )
            robot.connect(calibrate=False)
            self._buses[side] = robot

    def _require_connected(self) -> None:
        if not self._buses:
            raise RuntimeError("RealFollowerArms.connect() must be called first")

    def read_positions(self) -> list[float]:
        self._require_connected()
        per_side = {}
        for side in ARM_SIDES:
            obs = self._buses[side].get_observation()
            per_side[side] = {k: v for k, v in obs.items() if k.endswith(".pos")}
        return to_arrays(per_side["left"], per_side["right"])

    def write_positions(self, angles: Sequence[float]) -> None:
        self._require_connected()
        left, right = to_dicts(angles)
        self._buses["left"].send_action(left)
        self._buses["right"].send_action(right)

    def set_torque(self, enabled: bool) -> None:
        self._require_connected()
        for side in ARM_SIDES:
            bus = self._buses[side].bus
            if enabled:
                bus.enable_torque()
            else:
                bus.disable_torque()

    def close(self) -> None:
        for side, robot in list(self._buses.items()):
            try:
                robot.disconnect()
            except Exception:
                log.exception("follower %s: disconnect failed", side)
        self._buses.clear()
