"""안전 게이트 - 이 프로젝트에서 사고를 막는 로직 전부가 여기 있다.

의도적으로 **네트워크도 하드웨어도 건드리지 않는다.** 시각조차 인자로 받는다.
덕분에 로봇도 인터넷도 없이 100% 단위 테스트가 가능하다.
실험실 장비 앞에서 안전 로직을 처음 시험하는 상황을 만들지 않기 위한 설계다.

스펙 §5 참조.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.config import SafetyConfig
from common.protocol import JOINT_NAMES, N_JOINTS, Cmd, ControlPacket, Flag, State


@dataclass(frozen=True)
class SafetyResult:
    """한 틱의 판정 결과.

    targets 가 None 이면 팔로워에 아무것도 쓰지 않는다 (토크가 꺼진 상태).
    """

    state: State
    torque: bool
    targets: list[float] | None
    flags: int
    reason: str | None


class SafetyGate:
    def __init__(self, cfg: SafetyConfig) -> None:
        self._cfg = cfg
        self._state = State.DISCONNECTED
        # 마지막으로 팔로워에 '쓴' 각도. 실제각이 아니라 명령각이다.
        self._applied: list[float] | None = None
        self._last_packet_t: float | None = None
        self._prev_clutch = False
        self._reason: str | None = None
        self._follow_error_since: float | None = None

    @property
    def state(self) -> State:
        return self._state

    def force_hold(self, reason: str) -> None:
        """게이트 바깥의 사유(서보 통신 실패 등)로 HOLD 를 강제한다.

        여기서도 자동 복귀는 없다. 벗어나려면 RESET 이 필요하다.
        """
        if self._state is not State.HOLD:
            self._state = State.HOLD
            self._reason = reason

    def step(self, packet: ControlPacket | None, actual: list[float], now: float) -> SafetyResult:
        if len(actual) != N_JOINTS:
            raise ValueError(f"actual must have {N_JOINTS} elements, got {len(actual)}")

        flags = 0

        if packet is not None:
            self._last_packet_t = now

        # --- HOLD 는 어떤 경우에도 스스로 벗어나지 않는다 -------------------
        # 유일한 탈출구는 명시적 RESET 명령이다.
        if self._state is State.HOLD:
            if packet is not None and packet.cmd is Cmd.RESET:
                self._enter_aligning(actual)
            else:
                flags |= Flag.WATCHDOG if self._reason == "watchdog timeout" else 0
                return self._result(flags)

        # --- 워치독: 제어 패킷이 끊기면 즉시 HOLD --------------------------
        if self._state in (State.ALIGNING, State.ENGAGED):
            if self._last_packet_t is None or (now - self._last_packet_t) > self._cfg.watchdog_ms / 1000.0:
                return self._to_hold("watchdog timeout", Flag.WATCHDOG)

        # --- DISCONNECTED: 첫 유효 패킷을 기다린다 -------------------------
        if self._state is State.DISCONNECTED:
            if packet is None:
                return self._result(flags)
            self._enter_aligning(actual)

        if packet is None:
            # 패킷 없는 틱에서는 현재 목표를 유지하기만 한다.
            return self._result(flags)

        clutch_rising = packet.clutch and not self._prev_clutch
        self._prev_clutch = packet.clutch

        # --- ALIGNING: 리더를 팔로워 자세에 맞출 때까지 기다린다 -----------
        if self._state is State.ALIGNING:
            aligned = self._is_aligned(packet.joints, actual)
            if aligned and clutch_rising:
                self._state = State.ENGAGED
            else:
                return self._result(flags)

        # --- ENGAGED: 클러치를 놓으면 즉시 그 자리에서 정지 ----------------
        if self._state is State.ENGAGED:
            if not packet.clutch:
                self._state = State.ALIGNING
                return self._result(flags)
            flags |= self._follow(packet, actual, now)

        return self._result(flags)

    # ------------------------------------------------------------------ #

    def _follow(self, packet: ControlPacket, actual: list[float], now: float) -> int:
        """ENGAGED 에서 리더를 추종한다. Task 4 에서 클램프가 추가된다."""
        self._applied = list(packet.joints)
        return 0

    def _is_aligned(self, leader: tuple[float, ...], actual: list[float]) -> bool:
        threshold = self._cfg.align_threshold_deg
        return all(abs(leader[i] - actual[i]) < threshold for i in range(N_JOINTS))

    def _enter_aligning(self, actual: list[float]) -> None:
        self._state = State.ALIGNING
        self._applied = list(actual)
        self._reason = None
        self._follow_error_since = None
        # 리셋 직후 클러치가 눌린 채라면 상승 에지를 요구하기 위해 눌림으로 간주한다.
        self._prev_clutch = True

    def _to_hold(self, reason: str, flag: Flag) -> SafetyResult:
        self._state = State.HOLD
        self._reason = reason
        return self._result(int(flag))

    def _result(self, flags: int) -> SafetyResult:
        torque = self._state in (State.ALIGNING, State.ENGAGED, State.HOLD)
        targets = list(self._applied) if (torque and self._applied is not None) else None
        return SafetyResult(
            state=self._state, torque=torque, targets=targets, flags=flags, reason=self._reason
        )
