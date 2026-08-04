"""실물 리더 암 2대를 lerobot 없이 읽는다.

`home/leader_arms.py` (lerobot 판) 와 같은 자리에 끼워지는 구현이다. 숫자가 같다는
것은 `tools/compare_read.py` 로 실물에서 확인했다 - 관절 12개 전부 차이 0.000
(2026-08-04).

**왜 갈아끼우는가.** `lerobot.motors.feetech` 를 import 하면 `torch` 4.2GB 가 딸려와
exe 로 묶을 수 없다. 원격 사용자 3명에게 파이썬 환경을 세팅하게 할 수는 없으므로,
클라이언트에서 lerobot 을 걷어내는 것이 배포의 전제다.

lerobot 판은 지웠지 않고 남겨 둔다. `tools/compare_read.py` 가 대조에 쓰고, 앞으로도
"기준이 무엇이었나" 를 확인할 곳이 필요하기 때문이다.
"""

from __future__ import annotations

import logging
import time

from common.config import ArmConfig
from common.feetech_lite import (
    MOTOR_NAMES,
    FeetechLiteBus,
    MotorCalibration,
    MotorError,
    looks_uncalibrated,
    raw_to_degrees,
    raw_to_percent,
)
from common.joints import ARM_SIDES, require_both_sides
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)

#: lerobot 판과 같은 이유로 재시도한다. 1Mbaud 반이중 버스는 패킷을 흘린다.
_CONNECT_RETRIES = 4
_CONNECT_RETRY_DELAY = 0.5

#: 그리퍼는 퍼센트, 나머지는 도. MOTOR_NAMES 안에서의 자리.
_GRIPPER_INDEX = MOTOR_NAMES.index("gripper")


class LiteLeaderArms:
    """리더 암 2대. 위치를 읽고, 토크는 꺼 둔다."""

    def __init__(self, arms: dict[str, ArmConfig]) -> None:
        require_both_sides(arms)
        self._arms = arms
        self._buses: dict[str, FeetechLiteBus] = {}
        self._calibration: dict[str, dict[int, MotorCalibration]] = {}

    @property
    def is_connected(self) -> bool:
        return bool(self._buses)

    def calibration(self, side: str) -> dict[int, MotorCalibration]:
        """서보에서 읽어 둔 캘리브레이션. 진단 화면이 쓴다."""
        return self._calibration[side]

    def connect(self) -> None:
        # lerobot 판과 같은 이유로 **포트를 하나라도 열기 전에 전부 조회한다.**
        # 열린 포트가 있는 상태에서 Windows 장치 열거가 실패하는 것을 피한다.
        ports = {
            side: resolve_port_spec(self._arms[side].serial_number, self._arms[side].port)
            for side in ARM_SIDES
        }
        for side in ARM_SIDES:
            log.info("leader %s: opening %s (no lerobot)", side, ports[side])
            bus = self._connect_one(side, ports[side])
            self._buses[side] = bus
            self._calibration[side] = bus.read_calibration()
            self._warn_if_uncalibrated(side)

    def _connect_one(self, side: str, port: str) -> FeetechLiteBus:
        last_error: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            bus = FeetechLiteBus(port=port)
            try:
                bus.connect()
                # 사람이 손으로 움직일 수 있어야 한다. 켜진 채로는 팔이 굳어 있다.
                bus.disable_torque()
                bus.sync_read_positions()  # 6개가 다 대답하는지 여기서 확인한다
            except Exception as exc:
                last_error = exc
                log.warning(
                    "leader %s: connect attempt %d/%d failed on %s: %s",
                    side,
                    attempt,
                    _CONNECT_RETRIES,
                    port,
                    exc,
                )
                bus.close()
                time.sleep(_CONNECT_RETRY_DELAY)
                continue

            if attempt > 1:
                log.info("leader %s: connected on attempt %d", side, attempt)
            return bus

        raise ConnectionError(
            f"leader {side}: could not connect on {port} after {_CONNECT_RETRIES} attempts. "
            f"Last error: {last_error}. Check power and the daisy-chain cables."
        ) from last_error

    def _warn_if_uncalibrated(self, side: str) -> None:
        """캘리브레이션 안 된 팔로 조종을 시작하면 팔로워가 엉뚱한 자세로 간다."""
        bad = [
            MOTOR_NAMES[motor_id - 1]
            for motor_id, cal in sorted(self._calibration[side].items())
            if looks_uncalibrated(cal)
        ]
        if bad:
            log.warning(
                "leader %s: %s look never calibrated (factory range and zero offset). "
                "Angles from this arm will be wrong - calibrate before teleoperating.",
                side,
                ", ".join(bad),
            )

    def _require_connected(self) -> None:
        if not self._buses:
            raise RuntimeError("LiteLeaderArms.connect() must be called first")

    def read_positions(self) -> list[float]:
        """12칸 배열. 왼팔 6개 + 오른팔 6개, 그리퍼만 퍼센트."""
        self._require_connected()
        out: list[float] = []
        for side in ARM_SIDES:
            raws = self._buses[side].sync_read_positions()
            calibration = self._calibration[side]
            for index, _name in enumerate(MOTOR_NAMES):
                motor_id = index + 1
                cal = calibration[motor_id]
                raw = raws[motor_id]
                out.append(
                    raw_to_percent(raw, cal) if index == _GRIPPER_INDEX else raw_to_degrees(raw, cal)
                )
        return out

    def close(self) -> None:
        for side, bus in list(self._buses.items()):
            try:
                bus.close()
            except MotorError:
                log.exception("leader %s: close failed", side)
        self._buses.clear()
        self._calibration.clear()
