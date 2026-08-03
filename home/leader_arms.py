"""실물 리더 암 2대. lerobot 의 SOLeader 를 LeaderArms Protocol 로 감싼다.

리더는 읽기 전용이다. 토크를 끈 채로 두어 사람이 손으로 움직일 수 있게 한다.
"""

from __future__ import annotations

import logging
import time

from lerobot.teleoperators.so_leader import SOLeader, SOLeaderTeleopConfig

from common.config import ArmConfig
from common.joints import ARM_SIDES, require_both_sides, to_arrays
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)

#: 팔로워와 같은 이유로 재시도한다. lerobot 의 토크 제어는 num_retry=0 이라
#: 1Mbaud 반이중 버스에서 패킷 하나가 유실되면 connect() 가 죽는다.
_CONNECT_RETRIES = 4
_CONNECT_RETRY_DELAY = 0.5


class RealLeaderArms:
    def __init__(self, arms: dict[str, ArmConfig]) -> None:
        require_both_sides(arms)
        self._arms = arms
        self._buses: dict[str, SOLeader] = {}

    @property
    def is_connected(self) -> bool:
        return bool(self._buses)

    def connect(self) -> None:
        # 팔로워와 같은 이유로 **포트를 하나라도 열기 전에 전부 조회한다.**
        # 조회 실패를 하드웨어 접촉 전에 잡고, 열린 포트가 있는 상태에서
        # Windows 장치 열거가 실패하는 경우를 피한다.
        ports = {
            side: resolve_port_spec(self._arms[side].serial_number, self._arms[side].port)
            for side in ARM_SIDES
        }
        for side in ARM_SIDES:
            arm = self._arms[side]
            log.info(
                "leader %s: opening %s (calibration id %s)", side, ports[side], arm.calibration_id
            )
            self._buses[side] = self._connect_one(side, ports[side], arm)

    def _connect_one(self, side: str, port: str, arm: ArmConfig) -> SOLeader:
        """팔 한 대를 연결한다. 버스가 패킷을 흘리면 다시 시도한다."""
        last_error: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            teleop = SOLeader(
                SOLeaderTeleopConfig(port=port, id=arm.calibration_id, use_degrees=True)
            )
            try:
                teleop.connect(calibrate=False)
                # 사람이 손으로 움직일 수 있어야 한다. configure() 가 이미 끄지만
                # 의도를 코드로 남기고, 재시도를 줘 패킷 유실에 견디게 한다.
                teleop.bus.disable_torque(num_retry=5)
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
                try:
                    teleop.bus.disconnect(disable_torque=False)
                except Exception:
                    pass
                time.sleep(_CONNECT_RETRY_DELAY)
                continue

            if attempt > 1:
                log.info("leader %s: connected on attempt %d", side, attempt)
            return teleop

        raise ConnectionError(
            f"leader {side}: could not connect on {port} after {_CONNECT_RETRIES} attempts. "
            f"Last error: {last_error}. Check power and the daisy-chain cables; "
            f"'python -m tools.probe_hardware --scan-motors' shows which motors answer."
        ) from last_error

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
