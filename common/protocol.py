"""와이어 프로토콜 정의.

이 모듈은 집 PC와 작업대 PC가 **완전히 동일하게** 가지고 있어야 한다.
한쪽만 바뀌면 상대가 바이트를 오해석해 팔에 엉뚱한 각도가 들어간다.
구조를 변경하면 PROTOCOL_MAGIC 을 RT02 로 올린다 (스펙 §4.6).

네트워크·하드웨어 의존이 전혀 없는 순수 모듈이다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

#: RT02 에서 State.HOMING(5) 이 추가되었다. 와이어 레이아웃은 그대로지만, 구버전
#: 클라이언트는 state=5 를 해석할 수 없어 텔레메트리를 조용히 버린다. 그러면 조종자
#: 화면에는 '연결 끊김'처럼 보여 원인을 찾기 어렵다. 버전을 올려 큰 소리로 거부하게
#: 만드는 것이 magic 필드의 존재 이유다 (스펙 §4.6).
PROTOCOL_MAGIC = b"RT02"
VIDEO_MAGIC = b"RTV1"

N_JOINTS = 12

#: 관절 순서는 프로토콜의 일부다. 양쪽이 이 상수를 import 해서 쓴다.
JOINT_NAMES: tuple[str, ...] = (
    "left_shoulder_pan",
    "left_shoulder_lift",
    "left_elbow_flex",
    "left_wrist_flex",
    "left_wrist_roll",
    "left_gripper",
    "right_shoulder_pan",
    "right_shoulder_lift",
    "right_elbow_flex",
    "right_wrist_flex",
    "right_wrist_roll",
    "right_gripper",
)

# "<" = 리틀엔디안 + 정렬 패딩 없음. 빠뜨리면 크기가 달라진다.
CONTROL_FORMAT = "<4sIdBB12f"
TELEMETRY_FORMAT = "<4sIdBB12f"
VIDEO_HEADER_FORMAT = "<4sBIdI"

CONTROL_SIZE = struct.calcsize(CONTROL_FORMAT)  # 66
TELEMETRY_SIZE = struct.calcsize(TELEMETRY_FORMAT)  # 66
VIDEO_HEADER_SIZE = struct.calcsize(VIDEO_HEADER_FORMAT)  # 21

_UINT32 = 1 << 32


class State(IntEnum):
    """안전 상태머신의 상태 (스펙 §5.1)."""

    DISCONNECTED = 0
    ALIGNING = 1
    ENGAGED = 2
    HOLD = 3
    FAULT = 4
    #: HOLD 에서 리셋했을 때, 팔로워를 설정된 home 자세로 천천히 되돌리는 중.
    #: HOLD 가 걸린 자세가 리더로 도달 불가능하면 정렬 절차를 통과할 방법이 없기
    #: 때문에 필요하다 - 원격에서는 손으로 팔로워를 돌릴 수 없다 (스펙 §5.2).
    HOMING = 5


class Flag(IntFlag):
    """텔레메트리 경고 비트마스크 (스펙 §4.4)."""

    SPEED_CLAMPED = 1 << 0
    JOINT_LIMITED = 1 << 1
    FOLLOW_ERROR = 1 << 2
    WATCHDOG = 1 << 3
    MOTOR_ERROR = 1 << 4


class Cmd(IntEnum):
    """제어 패킷의 명령 필드. RESET 은 HOLD 상태에서만 유효하다."""

    NONE = 0
    RESET = 1


class CamId(IntEnum):
    FRONT = 0
    WRIST_LEFT = 1
    WRIST_RIGHT = 2


def is_newer(seq: int, last_seq: int) -> bool:
    """seq 가 last_seq 보다 새로운 패킷인가?

    UDP 는 순서를 보장하지 않으므로 늦게 도착한 낡은 패킷을 걸러내야 한다.
    uint32 되감김(42억 -> 0)을 고려한 순환 비교를 쓴다.
    """
    diff = (seq - last_seq) % _UINT32
    return 0 < diff < (_UINT32 // 2)


def _check_joints(joints: tuple[float, ...] | list[float]) -> None:
    if len(joints) != N_JOINTS:
        raise ValueError(f"joints must have {N_JOINTS} elements, got {len(joints)}")


@dataclass(frozen=True)
class ControlPacket:
    """집 -> 작업대. 66바이트, 60Hz."""

    seq: int
    t_send: float
    clutch: bool
    cmd: Cmd
    joints: tuple[float, ...]

    def pack(self) -> bytes:
        _check_joints(self.joints)
        return struct.pack(
            CONTROL_FORMAT,
            PROTOCOL_MAGIC,
            self.seq % _UINT32,
            self.t_send,
            1 if self.clutch else 0,
            int(self.cmd),
            *self.joints,
        )

    @staticmethod
    def unpack(data: bytes) -> ControlPacket | None:
        """유효하지 않으면 None 을 돌려준다 (예외를 던지지 않는다).

        60Hz 수신 루프에서 정체불명의 패킷 때문에 예외 처리가 돌지 않도록,
        거부는 조용한 None 반환으로 표현한다.
        """
        if len(data) != CONTROL_SIZE:
            return None
        magic, seq, t_send, clutch, cmd, *joints = struct.unpack(CONTROL_FORMAT, data)
        if magic != PROTOCOL_MAGIC:
            return None
        try:
            cmd_enum = Cmd(cmd)
        except ValueError:
            cmd_enum = Cmd.NONE  # 모르는 명령은 무시한다
        return ControlPacket(
            seq=seq,
            t_send=t_send,
            clutch=bool(clutch),
            cmd=cmd_enum,
            joints=tuple(joints),
        )


@dataclass(frozen=True)
class TelemetryPacket:
    """작업대 -> 집. 66바이트, 60Hz. joints 는 팔로워의 **실제** 각도."""

    seq_echo: int
    t_send: float
    state: State
    flags: int
    joints: tuple[float, ...]

    def pack(self) -> bytes:
        _check_joints(self.joints)
        return struct.pack(
            TELEMETRY_FORMAT,
            PROTOCOL_MAGIC,
            self.seq_echo % _UINT32,
            self.t_send,
            int(self.state),
            int(self.flags) & 0xFF,
            *self.joints,
        )

    @staticmethod
    def unpack(data: bytes) -> TelemetryPacket | None:
        if len(data) != TELEMETRY_SIZE:
            return None
        magic, seq_echo, t_send, state, flags, *joints = struct.unpack(TELEMETRY_FORMAT, data)
        if magic != PROTOCOL_MAGIC:
            return None
        try:
            state_enum = State(state)
        except ValueError:
            return None
        return TelemetryPacket(
            seq_echo=seq_echo,
            t_send=t_send,
            state=state_enum,
            flags=flags,
            joints=tuple(joints),
        )


@dataclass(frozen=True)
class VideoHeader:
    """영상 프레임의 21바이트 고정 헤더. 뒤에 length 바이트의 JPEG 이 따라온다.

    TCP 는 메시지 경계를 보존하지 않으므로 길이를 미리 실어 보낸다
    (length-prefixed framing, 스펙 §4.5).
    """

    cam_id: int
    seq: int
    t_capture: float
    length: int

    def pack(self) -> bytes:
        return struct.pack(
            VIDEO_HEADER_FORMAT,
            VIDEO_MAGIC,
            self.cam_id,
            self.seq % _UINT32,
            self.t_capture,
            self.length,
        )

    @staticmethod
    def unpack(data: bytes) -> VideoHeader | None:
        if len(data) != VIDEO_HEADER_SIZE:
            return None
        magic, cam_id, seq, t_capture, length = struct.unpack(VIDEO_HEADER_FORMAT, data)
        if magic != VIDEO_MAGIC:
            return None
        return VideoHeader(cam_id=cam_id, seq=seq, t_capture=t_capture, length=length)
