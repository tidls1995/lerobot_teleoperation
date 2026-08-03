"""실물 팔로워 암 2대. lerobot 의 SOFollower 를 FollowerArms Protocol 로 감싼다.

1단계에서 만든 서버는 이 Protocol 만 알고 있으므로, 설정 한 줄로 mock 과
갈아끼울 수 있다. 문제가 생겼을 때 mock 으로 돌려 '네트워크냐 하드웨어냐'를
즉시 가릴 수 있는 것이 이 구조의 목적이다.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

from common.config import ArmConfig
from common.joints import ARM_SIDES, require_both_sides, to_arrays, to_dicts
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)

#: 연결을 몇 번까지 다시 시도할 것인가.
#:
#: lerobot 의 ``enable_torque`` 는 ``num_retry=0`` 으로 쓰기 때문에, 1Mbaud 반이중
#: 버스에서 패킷 하나만 유실되면 connect() 전체가 ConnectionError 로 죽는다.
#: (실측: "Failed to write 'Lock' on id_=6 ... There is no status packet!" 이 뜬 직후
#: 모터 6개를 num_retry=2 로 핑하면 전부 응답했다.) lerobot 자신도 disconnect 경로에는
#: ``disable_torque(num_retry=5)`` 를 쓴다 - 켜는 경로에만 방어가 빠져 있다.
_CONNECT_RETRIES = 4
_CONNECT_RETRY_DELAY = 0.5


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
        # **포트를 하나라도 열기 전에 전부 조회한다.**
        #
        # 조회 실패는 하드웨어를 건드리기 전에 잡는 것이 맞다. 왼팔을 연 뒤에
        # 오른팔 조회가 실패하면 왼팔만 통전된 어중간한 상태로 죽는다.
        #
        # (한때 이 순서가 [WinError 87] 의 해결책이라고 적혀 있었다. 아니다.
        #  열린 포트는 열거와 무관하다 - 'probe_hardware --list-while-open' 로
        #  확인할 수 있다. 진짜 원인은 common/serial_ports.py 에 적어 두었다.)
        ports = {
            side: resolve_port_spec(self._arms[side].serial_number, self._arms[side].port)
            for side in ARM_SIDES
        }
        for side in ARM_SIDES:
            arm = self._arms[side]
            log.info(
                "follower %s: opening %s (calibration id %s)", side, ports[side], arm.calibration_id
            )
            self._buses[side] = self._connect_one(side, ports[side], arm)

    def _connect_one(self, side: str, port: str, arm: ArmConfig) -> SOFollower:
        """팔 한 대를 연결한다. 버스가 패킷을 흘리면 다시 시도한다."""
        last_error: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
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
            try:
                robot.connect(calibrate=False)
            except Exception as exc:
                last_error = exc
                log.warning(
                    "follower %s: connect attempt %d/%d failed on %s: %s",
                    side,
                    attempt,
                    _CONNECT_RETRIES,
                    port,
                    exc,
                )
                # 반쯤 열린 포트를 남기면 다음 시도가 '포트 사용 중'으로 실패한다.
                try:
                    robot.bus.disconnect(disable_torque=False)
                except Exception:
                    pass
                time.sleep(_CONNECT_RETRY_DELAY)
                continue

            # lerobot 의 configure() 는 torque_disabled() 컨텍스트를 쓰는데, 그 매니저는
            # "종료 시 토크를 반드시 다시 켠다"고 문서에 명시돼 있다. 즉 연결만 해도
            # 팔이 통전된다. 스펙 §5.1 의 DISCONNECTED 는 토크가 꺼진 상태이므로,
            # 여기서 명시적으로 끈다. 조종자가 아직 붙지도 않았는데 팔이 힘을 주고
            # 서 있을 이유가 없고, 작업대의 사람이 손으로 치울 수 있어야 한다.
            robot.bus.disable_torque(num_retry=5)
            if attempt > 1:
                log.info("follower %s: connected on attempt %d", side, attempt)
            return robot

        raise ConnectionError(
            f"follower {side}: could not connect on {port} after {_CONNECT_RETRIES} attempts. "
            f"Last error: {last_error}. Check power and the daisy-chain cables; "
            f"'python -m tools.probe_hardware --scan-motors' shows which motors answer."
        ) from last_error

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
