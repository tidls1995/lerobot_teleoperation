"""실물 리더 암 2대. lerobot 의 SOLeader 를 LeaderArms Protocol 로 감싼다.

리더는 읽기 전용이다. 토크를 끈 채로 두어 사람이 손으로 움직일 수 있게 한다.
"""

from __future__ import annotations

import logging

from lerobot.teleoperators.so_leader import SOLeader, SOLeaderTeleopConfig

from common.config import ArmConfig
from common.joints import ARM_SIDES, require_both_sides, to_arrays
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)


class RealLeaderArms:
    def __init__(self, arms: dict[str, ArmConfig]) -> None:
        require_both_sides(arms)
        self._arms = arms
        self._buses: dict[str, SOLeader] = {}

    @property
    def is_connected(self) -> bool:
        return bool(self._buses)

    def connect(self) -> None:
        for side in ARM_SIDES:
            arm = self._arms[side]
            port = resolve_port_spec(arm.serial_number, arm.port)
            log.info("leader %s: opening %s (calibration id %s)", side, port, arm.calibration_id)
            teleop = SOLeader(
                SOLeaderTeleopConfig(port=port, id=arm.calibration_id, use_degrees=True)
            )
            teleop.connect(calibrate=False)
            # 사람이 손으로 움직일 수 있어야 한다. configure() 가 이미 끄지만
            # 의도를 코드로 남긴다.
            teleop.disable_torque()
            self._buses[side] = teleop

    def _require_connected(self) -> None:
        if not self._buses:
            raise RuntimeError("RealLeaderArms.connect() must be called first")

    def read_positions(self) -> list[float]:
        self._require_connected()
        left = self._buses["left"].get_action()
        right = self._buses["right"].get_action()
        return to_arrays(left, right)

    def close(self) -> None:
        for side, teleop in list(self._buses.items()):
            try:
                teleop.disconnect()
            except Exception:
                log.exception("leader %s: disconnect failed", side)
        self._buses.clear()
