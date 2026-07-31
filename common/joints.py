"""lerobot 의 관절 dict 와 우리 프로토콜의 12칸 배열을 오간다.

lerobot 은 팔 한 대를 {"shoulder_pan.pos": 12.3, ...} 로 다루고, 우리 와이어
프로토콜은 양팔 12개를 한 배열로 다룬다. 이 변환이 틀리면 어깨 명령이 손목에
들어가므로, 하드웨어 없이 단위 테스트로 못 박아 둔다.

**단위 주의 (스펙 §4.3):** 배열의 5번과 11번은 그리퍼이며 단위가 도가 아니라
퍼센트(0~100)다. 이 모듈은 값을 변환하지 않고 자리만 옮기므로, 단위를 아는
것은 안전값을 정하는 쪽(config)의 책임이다.
"""

from __future__ import annotations

from typing import Sequence

from common.protocol import JOINT_NAMES, N_JOINTS

#: 배열에서 왼팔이 먼저, 오른팔이 나중이다.
ARM_SIDES: tuple[str, ...] = ("left", "right")

#: lerobot 의 SOLeader/SOFollower 가 쓰는 모터 이름과 그 순서.
MOTOR_NAMES: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)

_MOTORS_PER_ARM = len(MOTOR_NAMES)

#: 퍼센트(0~100) 단위인 자리. 도(degree)가 아니다.
GRIPPER_INDICES: tuple[int, ...] = tuple(
    i for i, name in enumerate(JOINT_NAMES) if name.endswith("gripper")
)


class JointNameError(Exception):
    """lerobot 이 준 관절 이름이 기대와 다르다."""


def arm_slice(side: str) -> slice:
    """그 팔이 12칸 배열에서 차지하는 구간."""
    try:
        index = ARM_SIDES.index(side)
    except ValueError as exc:
        raise JointNameError(f"unknown arm side {side!r}, expected one of {ARM_SIDES}") from exc
    start = index * _MOTORS_PER_ARM
    return slice(start, start + _MOTORS_PER_ARM)


def _one_arm_to_list(values: dict[str, float], side: str) -> list[float]:
    # lerobot 은 ".pos" 접미사를 붙이지만, 버스에서 직접 읽으면 붙지 않는다.
    # 양쪽을 다 받아준다.
    stripped = {k.removesuffix(".pos"): v for k, v in values.items()}

    unexpected = set(stripped) - set(MOTOR_NAMES)
    if unexpected:
        raise JointNameError(f"{side} arm returned unexpected motor(s): {sorted(unexpected)}")
    missing = set(MOTOR_NAMES) - set(stripped)
    if missing:
        raise JointNameError(f"{side} arm is missing motor(s): {sorted(missing)}")

    return [float(stripped[m]) for m in MOTOR_NAMES]


def to_arrays(left: dict[str, float], right: dict[str, float]) -> list[float]:
    """lerobot dict 2개를 12칸 배열로 합친다."""
    return _one_arm_to_list(left, "left") + _one_arm_to_list(right, "right")


def to_dicts(joints: Sequence[float]) -> tuple[dict[str, float], dict[str, float]]:
    """12칸 배열을 lerobot dict 2개로 나눈다."""
    if len(joints) != N_JOINTS:
        raise ValueError(f"joints must have {N_JOINTS} elements, got {len(joints)}")
    out = []
    for side in ARM_SIDES:
        chunk = joints[arm_slice(side)]
        out.append({f"{m}.pos": float(v) for m, v in zip(MOTOR_NAMES, chunk)})
    return out[0], out[1]


def require_both_sides(arms: dict[str, object]) -> None:
    """좌우 둘 다 있는지 검증한다. 리더·팔로워 어댑터가 함께 쓴다.

    한쪽만 있는 상태로 조종을 시작하면 배열의 절반이 쓰레기값이 된다.
    """
    missing = set(ARM_SIDES) - set(arms)
    if missing:
        raise ValueError(f"arms is missing side(s): {sorted(missing)}")
