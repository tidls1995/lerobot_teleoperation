# 1단계(mock) 원격 텔레오퍼레이션 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로봇 하드웨어와 카메라 없이, mock 장치만으로 localhost에서 완전히 동작하는 원격 텔레오퍼레이션 소프트웨어 전체를 완성한다.

**Architecture:** 제어는 UDP(66바이트 패킷, 60Hz), 영상은 TCP(길이 프리픽스 프레이밍, 3채널 다중화)로 완전히 분리한다. 안전 로직(`safety.py`)과 직렬화(`protocol.py`)는 네트워크·하드웨어 의존이 없는 순수 함수로 격리해 하드웨어 없이 100% 테스트한다. 하드웨어 경계는 `common/devices.py`의 Protocol로 정의하고, 1단계에서는 mock 구현만 사용한다.

**Tech Stack:** Python 3.12, pygame(HUD·키입력), opencv-python-headless(JPEG 인코딩/디코딩), PyYAML, numpy, pytest

**설계 스펙:** [`docs/specs/2026-07-31-remote-teleoperation-design.md`](../specs/2026-07-31-remote-teleoperation-design.md)

**범위:** 스펙 §11의 **1단계만**. 실제 SO-101 팔·카메라 어댑터(`follower_arms.py`, `leader_arms.py`, 실카메라)와 캘리브레이션·COM 포트 조회는 2단계 계획에서 다룬다. 그것들은 실물 없이는 테스트할 수 없기 때문이다.

---

## Global Constraints

- Python 인터프리터는 항상 `C:/Users/flash/miniconda3/envs/lerobot/python.exe`. 시스템 `python`을 쓰지 않는다.
- 작업 디렉터리는 `C:/Users/flash/Desktop/lerobot/remote teleoperation` (**경로에 공백 있음 — 모든 명령에서 반드시 따옴표로 감쌀 것**).
- 프로토콜 상수는 스펙 §4에서 그대로 가져온다: `CONTROL_SIZE=66`, `TELEMETRY_SIZE=66`, `VIDEO_HEADER_SIZE=21`, `PROTOCOL_MAGIC=b"RT01"`, `VIDEO_MAGIC=b"RTV1"`, `N_JOINTS=12`.
- `struct` 포맷은 반드시 `<` 접두(리틀엔디안 + 정렬 패딩 없음)로 시작한다. 빠뜨리면 크기가 달라진다.
- **와이어에 실리는 시각은 `time.time()`(epoch), 내부 타이밍 판정은 `time.monotonic()`.** 절대 섞지 않는다. 워치독·추종오차·프레임 폐기는 전부 monotonic.
- 기본 안전값(스펙 §13): 정렬 임계값 3.0도, 속도 클램프 **1.5도/프레임(90도/초)**, 추종오차 15.0도/500ms, 서버 워치독 200ms, 클라이언트 워치독 300ms.
- 영상 기본값: 320×240, 15fps, JPEG 품질 80, 카메라 3대.
- 포트: UDP 5555(제어+텔레메트리), TCP 5556(영상).
- **어떤 오류에서도 로봇이 사람 확인 없이 자동으로 움직임을 재개하지 않는다.** HOLD를 벗어나는 유일한 경로는 `cmd=RESET`이다.
- 커밋 메시지는 영문 Conventional Commits (`feat:`, `test:`, `fix:`, `chore:`).

---

## 파일 구조

| 파일 | 책임 | 외부 의존 |
|---|---|---|
| `common/protocol.py` | 패킷 정의, pack/unpack, 상수, 시퀀스 비교 | 없음 |
| `common/config.py` | YAML → 데이터클래스, 검증 | PyYAML |
| `common/devices.py` | 하드웨어 경계 Protocol (팔·카메라) | 없음 |
| `common/netutil.py` | `recv_exactly` 등 소켓 헬퍼 | 없음 |
| `workbench/safety.py` | 안전 게이트 (상태머신 + 클램프) | 없음 |
| `workbench/camera_pub.py` | 캡처·JPEG·1슬롯 버퍼·TCP 송신 | cv2, socket |
| `workbench/server.py` | 제어 루프, UDP, 조립 | 전부 |
| `home/video_recv.py` | TCP 수신·프레이밍 해제·디코딩·재접속 | cv2, socket |
| `home/hud.py` | pygame 화면·키 입력 | pygame |
| `home/client.py` | 리더 루프, UDP 송수신, 조립 | 전부 |
| `mock/fake_arms.py` | 하드웨어 없는 팔 대역 | 없음 |
| `mock/fake_cameras.py` | 합성 영상 생성 | cv2, numpy |

스펙 §6 대비 추가된 파일은 `common/devices.py`(하드웨어 경계를 한 곳에 모음)와 `common/netutil.py`(`recv_exactly`를 송·수신 양쪽에서 재사용) 두 개다.

---

### Task 1: 프로젝트 스캐폴딩 + 프로토콜

**Files:**
- Create: `.gitignore`, `requirements.txt`, `README.md`
- Create: `common/__init__.py`, `workbench/__init__.py`, `home/__init__.py`, `mock/__init__.py`, `tests/__init__.py`
- Create: `common/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces:
  - 상수 `PROTOCOL_MAGIC: bytes`, `VIDEO_MAGIC: bytes`, `N_JOINTS: int = 12`, `JOINT_NAMES: tuple[str, ...]`, `CONTROL_SIZE=66`, `TELEMETRY_SIZE=66`, `VIDEO_HEADER_SIZE=21`
  - `class State(IntEnum)`: `DISCONNECTED=0, ALIGNING=1, ENGAGED=2, HOLD=3, FAULT=4`
  - `class Flag(IntFlag)`: `SPEED_CLAMPED=1, JOINT_LIMITED=2, FOLLOW_ERROR=4, WATCHDOG=8, MOTOR_ERROR=16`
  - `class Cmd(IntEnum)`: `NONE=0, RESET=1`
  - `class CamId(IntEnum)`: `FRONT=0, WRIST_LEFT=1, WRIST_RIGHT=2`
  - `ControlPacket(seq: int, t_send: float, clutch: bool, cmd: Cmd, joints: tuple[float, ...])` — `.pack() -> bytes`, `.unpack(data: bytes) -> ControlPacket | None`
  - `TelemetryPacket(seq_echo: int, t_send: float, state: State, flags: int, joints: tuple[float, ...])` — 동일한 `.pack()` / `.unpack()`
  - `VideoHeader(cam_id: int, seq: int, t_capture: float, length: int)` — 동일한 `.pack()` / `.unpack()`
  - `is_newer(seq: int, last_seq: int) -> bool`

- [ ] **Step 1: git 저장소와 디렉터리 뼈대를 만든다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git init && mkdir -p common workbench home mock tests config
```

`.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
*.log
```

`requirements.txt`:

```
numpy>=2.0
opencv-python-headless>=4.10
PyYAML>=6.0
pygame>=2.6
pytest>=8.0
```

`README.md`:

```markdown
# SO-101 Remote Teleoperation

집의 리더 암 2대로 작업대의 팔로워 암 2대를 카메라 영상을 보며 원격 조작한다.

설계: `docs/specs/2026-07-31-remote-teleoperation-design.md`
계획: `docs/plans/2026-07-31-stage1-mock-teleoperation.md`

## 실행 (1단계 mock)

두 개의 터미널에서:

    python -m workbench.server --config config/workbench.yaml
    python -m home.client --config config/home.yaml

## 테스트

    python -m pytest tests/ -v
```

빈 `__init__.py` 5개를 만든다:

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && touch common/__init__.py workbench/__init__.py home/__init__.py mock/__init__.py tests/__init__.py
```

- [ ] **Step 2: 누락된 패키지를 설치한다**

```bash
"C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pip install pytest pygame
```

기대: 성공. `numpy`, `PyYAML`, `opencv-python-headless`는 이미 설치되어 있으므로 건드리지 않는다.

- [ ] **Step 3: 실패하는 테스트를 작성한다**

`tests/test_protocol.py`:

```python
import struct

import pytest

from common.protocol import (
    CONTROL_SIZE,
    N_JOINTS,
    PROTOCOL_MAGIC,
    TELEMETRY_SIZE,
    VIDEO_HEADER_SIZE,
    VIDEO_MAGIC,
    Cmd,
    ControlPacket,
    Flag,
    JOINT_NAMES,
    State,
    TelemetryPacket,
    VideoHeader,
    is_newer,
)

ANGLES = tuple(float(i) * 1.5 - 8.0 for i in range(N_JOINTS))


def test_joint_names_count_matches_n_joints():
    assert len(JOINT_NAMES) == N_JOINTS
    assert len(set(JOINT_NAMES)) == N_JOINTS


def test_control_packet_size_is_66():
    p = ControlPacket(seq=1, t_send=1.0, clutch=True, cmd=Cmd.NONE, joints=ANGLES)
    assert len(p.pack()) == CONTROL_SIZE == 66


def test_control_packet_roundtrip():
    p = ControlPacket(seq=123456, t_send=1753502841.5, clutch=True, cmd=Cmd.RESET, joints=ANGLES)
    got = ControlPacket.unpack(p.pack())
    assert got is not None
    assert got.seq == p.seq
    assert got.clutch is True
    assert got.cmd == Cmd.RESET
    assert got.t_send == pytest.approx(p.t_send, abs=1e-6)
    assert got.joints == pytest.approx(ANGLES, abs=1e-3)


def test_control_packet_rejects_wrong_magic():
    data = bytearray(ControlPacket(1, 1.0, False, Cmd.NONE, ANGLES).pack())
    data[0:4] = b"XXXX"
    assert ControlPacket.unpack(bytes(data)) is None


def test_control_packet_rejects_truncated():
    data = ControlPacket(1, 1.0, False, Cmd.NONE, ANGLES).pack()
    assert ControlPacket.unpack(data[:-1]) is None
    assert ControlPacket.unpack(data + b"\x00") is None


def test_telemetry_packet_size_and_roundtrip():
    flags = int(Flag.SPEED_CLAMPED | Flag.FOLLOW_ERROR)
    p = TelemetryPacket(seq_echo=99, t_send=2.5, state=State.ENGAGED, flags=flags, joints=ANGLES)
    assert len(p.pack()) == TELEMETRY_SIZE == 66
    got = TelemetryPacket.unpack(p.pack())
    assert got is not None
    assert got.seq_echo == 99
    assert got.state is State.ENGAGED
    assert got.flags == flags
    assert got.joints == pytest.approx(ANGLES, abs=1e-3)


def test_video_header_size_and_roundtrip():
    h = VideoHeader(cam_id=2, seq=7, t_capture=10.25, length=12345)
    assert len(h.pack()) == VIDEO_HEADER_SIZE == 21
    got = VideoHeader.unpack(h.pack())
    assert got is not None
    assert (got.cam_id, got.seq, got.length) == (2, 7, 12345)
    assert got.t_capture == pytest.approx(10.25)


def test_video_header_rejects_wrong_magic():
    data = bytearray(VideoHeader(0, 1, 1.0, 10).pack())
    data[0:4] = b"ZZZZ"
    assert VideoHeader.unpack(bytes(data)) is None


def test_magics_are_distinct_four_bytes():
    assert PROTOCOL_MAGIC == b"RT01"
    assert VIDEO_MAGIC == b"RTV1"
    assert len(PROTOCOL_MAGIC) == len(VIDEO_MAGIC) == 4


def test_control_packet_rejects_wrong_joint_count():
    with pytest.raises(ValueError):
        ControlPacket(1, 1.0, False, Cmd.NONE, (0.0,) * 5).pack()


def test_is_newer_normal_progression():
    assert is_newer(11, 10) is True
    assert is_newer(10, 10) is False
    assert is_newer(9, 10) is False


def test_is_newer_handles_uint32_wraparound():
    # 42억을 넘어 0으로 되감긴 경우도 '더 새로움'으로 판정해야 한다
    assert is_newer(2, 0xFFFFFFFE) is True
    assert is_newer(0xFFFFFFFE, 2) is False


def test_state_and_flag_values_match_spec():
    assert (State.DISCONNECTED, State.ALIGNING, State.ENGAGED, State.HOLD, State.FAULT) == (0, 1, 2, 3, 4)
    assert (Flag.SPEED_CLAMPED, Flag.JOINT_LIMITED, Flag.FOLLOW_ERROR) == (1, 2, 4)
    assert (Flag.WATCHDOG, Flag.MOTOR_ERROR) == (8, 16)
```

- [ ] **Step 4: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_protocol.py -v
```

기대: `ModuleNotFoundError: No module named 'common.protocol'` 로 수집 단계에서 실패.

- [ ] **Step 5: `common/protocol.py`를 구현한다**

```python
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

PROTOCOL_MAGIC = b"RT01"
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

CONTROL_SIZE = struct.calcsize(CONTROL_FORMAT)        # 66
TELEMETRY_SIZE = struct.calcsize(TELEMETRY_FORMAT)    # 66
VIDEO_HEADER_SIZE = struct.calcsize(VIDEO_HEADER_FORMAT)  # 21

_UINT32 = 1 << 32


class State(IntEnum):
    """안전 상태머신의 상태 (스펙 §5.1)."""

    DISCONNECTED = 0
    ALIGNING = 1
    ENGAGED = 2
    HOLD = 3
    FAULT = 4


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
    uint32 되감김(42억 → 0)을 고려한 순환 비교를 쓴다.
    """
    diff = (seq - last_seq) % _UINT32
    return 0 < diff < (_UINT32 // 2)


def _check_joints(joints: tuple[float, ...] | list[float]) -> None:
    if len(joints) != N_JOINTS:
        raise ValueError(f"joints must have {N_JOINTS} elements, got {len(joints)}")


@dataclass(frozen=True)
class ControlPacket:
    """집 → 작업대. 66바이트, 60Hz."""

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
    """작업대 → 집. 66바이트, 60Hz. joints 는 팔로워의 **실제** 각도."""

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
            VIDEO_HEADER_FORMAT, VIDEO_MAGIC, self.cam_id, self.seq % _UINT32, self.t_capture, self.length
        )

    @staticmethod
    def unpack(data: bytes) -> VideoHeader | None:
        if len(data) != VIDEO_HEADER_SIZE:
            return None
        magic, cam_id, seq, t_capture, length = struct.unpack(VIDEO_HEADER_FORMAT, data)
        if magic != VIDEO_MAGIC:
            return None
        return VideoHeader(cam_id=cam_id, seq=seq, t_capture=t_capture, length=length)
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_protocol.py -v
```

기대: 13개 테스트 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add wire protocol with control, telemetry and video packets"
```

---

### Task 2: 설정 로딩

**Files:**
- Create: `common/config.py`, `config/workbench.yaml`, `config/home.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `common.protocol.JOINT_NAMES`, `N_JOINTS`
- Produces:
  - `class ConfigError(Exception)`
  - `CameraConfig(id: int, name: str, index: int, width: int, height: int, fps: int, jpeg_quality: int)`
  - `SafetyConfig(align_threshold_deg: float, max_step_deg: float, follow_error_deg: float, follow_error_hold_ms: int, watchdog_ms: int, joint_limits: dict[str, tuple[float, float]])`
  - `WorkbenchConfig(use_mock: bool, control_port: int, video_port: int, cameras: list[CameraConfig], safety: SafetyConfig)`
  - `HomeConfig(server_host: str, control_port: int, video_port: int, use_mock: bool, client_watchdog_ms: int)`
  - `load_workbench_config(path: str | Path) -> WorkbenchConfig`
  - `load_home_config(path: str | Path) -> HomeConfig`

> 2단계에서 실물 팔의 `arms:` 섹션(시리얼 번호 → COM 포트)이 추가된다. 1단계는 mock만 쓰므로 넣지 않는다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_config.py`:

```python
import pytest

from common.config import ConfigError, load_home_config, load_workbench_config
from common.protocol import JOINT_NAMES

# YAML 은 들여쓰기가 곧 구조다. textwrap.dedent 를 쓰면 삽입된 블록과 본문의
# 들여쓰기가 서로 다르게 깎여 계층이 무너지므로, 여기서는 그대로 적는다.
LIMIT_INDENT = "    "
FULL_LIMITS = "\n".join(f"{LIMIT_INDENT}{name}: [-120.0, 120.0]" for name in JOINT_NAMES)

WORKBENCH_YAML = f"""use_mock: true
control_port: 5555
video_port: 5556
cameras:
  - {{ id: 0, name: front,       index: 0, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
  - {{ id: 1, name: wrist_left,  index: 1, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
  - {{ id: 2, name: wrist_right, index: 2, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
safety:
  align_threshold_deg: 3.0
  max_step_deg: 1.5
  follow_error_deg: 15.0
  follow_error_hold_ms: 500
  watchdog_ms: 200
  joint_limits:
{FULL_LIMITS}
"""

HOME_YAML = """server_host: "127.0.0.1"
control_port: 5555
video_port: 5556
use_mock: true
client_watchdog_ms: 300
"""


def limit_line(joint_name: str) -> str:
    return f"{LIMIT_INDENT}{joint_name}: [-120.0, 120.0]"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_workbench_config(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", WORKBENCH_YAML))
    assert cfg.use_mock is True
    assert cfg.control_port == 5555
    assert cfg.video_port == 5556
    assert len(cfg.cameras) == 3
    assert cfg.cameras[1].name == "wrist_left"
    assert cfg.cameras[0].width == 320
    assert cfg.safety.max_step_deg == 1.5
    assert cfg.safety.watchdog_ms == 200
    assert cfg.safety.joint_limits["left_gripper"] == (-120.0, 120.0)


def test_load_home_config(tmp_path):
    cfg = load_home_config(_write(tmp_path, "h.yaml", HOME_YAML))
    assert cfg.server_host == "127.0.0.1"
    assert cfg.client_watchdog_ms == 300
    assert cfg.use_mock is True


def test_missing_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(limit_line(JOINT_NAMES[3]) + "\n", "")
    with pytest.raises(ConfigError, match=JOINT_NAMES[3]):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_unknown_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(
        limit_line(JOINT_NAMES[0]), f"{LIMIT_INDENT}not_a_joint: [-120.0, 120.0]"
    )
    with pytest.raises(ConfigError, match="not_a_joint"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_inverted_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(
        limit_line(JOINT_NAMES[0]), f"{LIMIT_INDENT}{JOINT_NAMES[0]}: [50.0, 10.0]"
    )
    with pytest.raises(ConfigError, match="min .* max"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_duplicate_camera_id_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace("{ id: 1, name: wrist_left", "{ id: 0, name: wrist_left")
    with pytest.raises(ConfigError, match="camera id"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_bad_port_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace("control_port: 5555", "control_port: 99999")
    with pytest.raises(ConfigError, match="port"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_missing_required_key_is_rejected(tmp_path):
    broken = HOME_YAML.replace('server_host: "127.0.0.1"\n', "")
    with pytest.raises(ConfigError, match="server_host"):
        load_home_config(_write(tmp_path, "h.yaml", broken))


def test_shipped_config_files_load():
    """리포지토리에 들어 있는 실제 설정 파일이 유효해야 한다."""
    w = load_workbench_config("config/workbench.yaml")
    h = load_home_config("config/home.yaml")
    assert len(w.cameras) == 3
    assert w.control_port == h.control_port
    assert w.video_port == h.video_port
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_config.py -v
```

기대: `ModuleNotFoundError: No module named 'common.config'`.

- [ ] **Step 3: `common/config.py`를 구현한다**

```python
"""YAML 설정 파일을 데이터클래스로 읽어들이고 검증한다.

검증을 여기서 강하게 하는 이유: 관절 한계가 하나라도 빠지면 그 관절에는
안전 클램프가 걸리지 않는다. 조용히 통과시키는 것보다 기동 시 죽는 편이 낫다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from common.protocol import JOINT_NAMES


class ConfigError(Exception):
    """설정 파일이 유효하지 않다."""


@dataclass(frozen=True)
class CameraConfig:
    id: int
    name: str
    index: int
    width: int
    height: int
    fps: int
    jpeg_quality: int


@dataclass(frozen=True)
class SafetyConfig:
    align_threshold_deg: float
    max_step_deg: float
    follow_error_deg: float
    follow_error_hold_ms: int
    watchdog_ms: int
    joint_limits: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class WorkbenchConfig:
    use_mock: bool
    control_port: int
    video_port: int
    cameras: list[CameraConfig]
    safety: SafetyConfig


@dataclass(frozen=True)
class HomeConfig:
    server_host: str
    control_port: int
    video_port: int
    use_mock: bool
    client_watchdog_ms: int


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {p}")
    return data


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required key '{key}' in {where}")
    return data[key]


def _check_port(value: Any, key: str) -> int:
    if not isinstance(value, int) or not (1024 <= value <= 65535):
        raise ConfigError(f"{key}: port must be an integer in 1024..65535, got {value!r}")
    return value


def _parse_joint_limits(raw: Any) -> dict[str, tuple[float, float]]:
    if not isinstance(raw, dict):
        raise ConfigError("safety.joint_limits must be a mapping of joint name to [min, max]")

    unknown = set(raw) - set(JOINT_NAMES)
    if unknown:
        raise ConfigError(f"unknown joint name(s) in joint_limits: {sorted(unknown)}")

    missing = set(JOINT_NAMES) - set(raw)
    if missing:
        raise ConfigError(f"joint_limits is missing entries for: {sorted(missing)}")

    limits: dict[str, tuple[float, float]] = {}
    for name in JOINT_NAMES:
        pair = raw[name]
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise ConfigError(f"joint_limits[{name}] must be [min, max], got {pair!r}")
        lo, hi = float(pair[0]), float(pair[1])
        if lo >= hi:
            raise ConfigError(f"joint_limits[{name}]: min ({lo}) must be less than max ({hi})")
        limits[name] = (lo, hi)
    return limits


def _parse_safety(raw: Any) -> SafetyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("safety section must be a mapping")
    return SafetyConfig(
        align_threshold_deg=float(_require(raw, "align_threshold_deg", "safety")),
        max_step_deg=float(_require(raw, "max_step_deg", "safety")),
        follow_error_deg=float(_require(raw, "follow_error_deg", "safety")),
        follow_error_hold_ms=int(_require(raw, "follow_error_hold_ms", "safety")),
        watchdog_ms=int(_require(raw, "watchdog_ms", "safety")),
        joint_limits=_parse_joint_limits(_require(raw, "joint_limits", "safety")),
    )


def _parse_cameras(raw: Any) -> list[CameraConfig]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("cameras must be a non-empty list")
    cams: list[CameraConfig] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"camera entry must be a mapping, got {entry!r}")
        cam = CameraConfig(
            id=int(_require(entry, "id", "camera")),
            name=str(_require(entry, "name", "camera")),
            index=int(_require(entry, "index", "camera")),
            width=int(_require(entry, "width", "camera")),
            height=int(_require(entry, "height", "camera")),
            fps=int(_require(entry, "fps", "camera")),
            jpeg_quality=int(_require(entry, "jpeg_quality", "camera")),
        )
        if cam.id in seen:
            raise ConfigError(f"duplicate camera id: {cam.id}")
        if not (0 <= cam.jpeg_quality <= 100):
            raise ConfigError(f"camera {cam.id}: jpeg_quality must be 0..100")
        if cam.fps <= 0:
            raise ConfigError(f"camera {cam.id}: fps must be positive")
        seen.add(cam.id)
        cams.append(cam)
    return cams


def load_workbench_config(path: str | Path) -> WorkbenchConfig:
    data = _read_yaml(path)
    return WorkbenchConfig(
        use_mock=bool(_require(data, "use_mock", "workbench config")),
        control_port=_check_port(_require(data, "control_port", "workbench config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "workbench config"), "video_port"),
        cameras=_parse_cameras(_require(data, "cameras", "workbench config")),
        safety=_parse_safety(_require(data, "safety", "workbench config")),
    )


def load_home_config(path: str | Path) -> HomeConfig:
    data = _read_yaml(path)
    return HomeConfig(
        server_host=str(_require(data, "server_host", "home config")),
        control_port=_check_port(_require(data, "control_port", "home config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "home config"), "video_port"),
        use_mock=bool(_require(data, "use_mock", "home config")),
        client_watchdog_ms=int(_require(data, "client_watchdog_ms", "home config")),
    )
```

- [ ] **Step 4: 실제 설정 파일 2개를 만든다**

`config/workbench.yaml` — 관절 한계는 1단계에서는 mock 팔의 동작 범위를 넉넉히 덮는 값이다. **2단계 실기 검증에서 실제 장비 배치를 보고 확정한다.**

```yaml
# 작업대(서버) 설정
use_mock: true          # 1단계: mock 장치 사용. 2단계에서 false 로.
control_port: 5555      # UDP - 제어 + 텔레메트리
video_port: 5556        # TCP - 영상 3채널 다중화

cameras:
  - { id: 0, name: front,       index: 0, width: 320, height: 240, fps: 15, jpeg_quality: 80 }
  - { id: 1, name: wrist_left,  index: 1, width: 320, height: 240, fps: 15, jpeg_quality: 80 }
  - { id: 2, name: wrist_right, index: 2, width: 320, height: 240, fps: 15, jpeg_quality: 80 }

safety:
  align_threshold_deg: 3.0      # ENGAGED 진입에 필요한 최대 정렬 오차
  max_step_deg: 1.5             # 프레임당 최대 이동. 60Hz 기준 90도/초
  follow_error_deg: 15.0        # 이 이상 벌어지면 걸림 의심
  follow_error_hold_ms: 500     # 그 상태가 이만큼 지속되면 HOLD
  watchdog_ms: 200              # 제어 패킷 무수신 허용 시간
  joint_limits:                 # 1단계 임시값. 2단계에서 실제 장비 배치를 보고 확정한다.
    left_shoulder_pan:   [-120.0, 120.0]
    left_shoulder_lift:  [-120.0, 120.0]
    left_elbow_flex:     [-120.0, 120.0]
    left_wrist_flex:     [-120.0, 120.0]
    left_wrist_roll:     [-120.0, 120.0]
    left_gripper:        [-120.0, 120.0]
    right_shoulder_pan:  [-120.0, 120.0]
    right_shoulder_lift: [-120.0, 120.0]
    right_elbow_flex:    [-120.0, 120.0]
    right_wrist_flex:    [-120.0, 120.0]
    right_wrist_roll:    [-120.0, 120.0]
    right_gripper:       [-120.0, 120.0]
```

`config/home.yaml`:

```yaml
# 집(클라이언트) 설정
server_host: "127.0.0.1"   # 1단계: localhost. 4단계에서 작업대 공인 IP 로.
control_port: 5555
video_port: 5556
use_mock: true             # 1단계: mock 리더 암 사용
client_watchdog_ms: 300    # 텔레메트리 무수신 시 화면 경고까지의 시간
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_config.py -v
```

기대: 9개 테스트 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add YAML config loading with joint limit validation"
```

---

### Task 3: 안전 게이트 — 상태머신

**Files:**
- Create: `workbench/safety.py`
- Test: `tests/test_safety_states.py`

**Interfaces:**
- Consumes: `common.protocol` (`State`, `Flag`, `Cmd`, `ControlPacket`, `N_JOINTS`, `JOINT_NAMES`), `common.config.SafetyConfig`
- Produces:
  - `SafetyResult(state: State, torque: bool, targets: list[float] | None, flags: int, reason: str | None)`
  - `class SafetyGate:`
    - `__init__(self, cfg: SafetyConfig)`
    - `step(self, packet: ControlPacket | None, actual: list[float], now: float) -> SafetyResult`
    - 읽기 전용 속성 `state -> State`

`now`는 항상 `time.monotonic()` 기준의 초 단위 실수다. 호출자가 넘겨주므로 테스트에서 실제 대기 없이 시간을 진행시킬 수 있다.

이 작업은 상태 전이만 다룬다. 클램프는 Task 4에서 같은 파일에 추가한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_safety_states.py`:

```python
import pytest

from common.config import SafetyConfig
from common.protocol import JOINT_NAMES, N_JOINTS, Cmd, ControlPacket, Flag, State
from workbench.safety import SafetyGate

ZEROS = [0.0] * N_JOINTS


def make_config(**overrides) -> SafetyConfig:
    base = dict(
        align_threshold_deg=3.0,
        max_step_deg=1.5,
        follow_error_deg=15.0,
        follow_error_hold_ms=500,
        watchdog_ms=200,
        joint_limits={name: (-120.0, 120.0) for name in JOINT_NAMES},
    )
    base.update(overrides)
    return SafetyConfig(**base)


def packet(joints=None, clutch=False, cmd=Cmd.NONE, seq=1) -> ControlPacket:
    return ControlPacket(
        seq=seq, t_send=0.0, clutch=clutch, cmd=cmd, joints=tuple(joints if joints else ZEROS)
    )


def test_starts_disconnected_with_torque_off():
    gate = SafetyGate(make_config())
    result = gate.step(None, ZEROS, now=0.0)
    assert result.state is State.DISCONNECTED
    assert result.torque is False
    assert result.targets is None


def test_first_packet_moves_to_aligning():
    gate = SafetyGate(make_config())
    result = gate.step(packet(), ZEROS, now=0.0)
    assert result.state is State.ALIGNING
    assert result.torque is True
    assert result.targets == pytest.approx(ZEROS)


def test_aligning_holds_the_pose_it_entered_with():
    gate = SafetyGate(make_config())
    start = [10.0] * N_JOINTS
    gate.step(packet(), start, now=0.0)
    # 리더가 멀리 있어도 팔로워는 진입 시점 자세를 유지해야 한다
    result = gate.step(packet(joints=[90.0] * N_JOINTS, seq=2), start, now=0.02)
    assert result.state is State.ALIGNING
    assert result.targets == pytest.approx(start)


def test_does_not_engage_while_alignment_error_is_too_large():
    gate = SafetyGate(make_config())
    gate.step(packet(), ZEROS, now=0.0)
    far = [0.0] * N_JOINTS
    far[3] = 10.0  # 임계값 3도를 넘는 관절이 하나라도 있으면 안 된다
    result = gate.step(packet(joints=far, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ALIGNING


def test_engages_when_aligned_and_clutch_pressed():
    gate = SafetyGate(make_config())
    gate.step(packet(), ZEROS, now=0.0)
    near = [1.0] * N_JOINTS  # 전부 3도 이내
    result = gate.step(packet(joints=near, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ENGAGED


def test_clutch_must_be_a_rising_edge():
    """이미 눌린 채로 정렬에 성공해도 engage 되면 안 된다 (스펙 §5.2)."""
    gate = SafetyGate(make_config())
    far = [0.0] * N_JOINTS
    far[0] = 30.0
    # 클러치를 누른 채 크게 어긋난 상태로 진입
    gate.step(packet(joints=far, clutch=True), ZEROS, now=0.0)
    # 누른 채로 정렬이 맞아떨어져도 engage 되지 않아야 한다
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=2), ZEROS, now=0.02)
    assert result.state is State.ALIGNING
    # 놓았다가
    gate.step(packet(joints=ZEROS, clutch=False, seq=3), ZEROS, now=0.04)
    # 다시 누르면 engage
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=4), ZEROS, now=0.06)
    assert result.state is State.ENGAGED


def _engage(gate, now=0.0):
    gate.step(packet(), ZEROS, now=now)
    gate.step(packet(joints=ZEROS, clutch=True, seq=2), ZEROS, now=now + 0.02)
    assert gate.state is State.ENGAGED
    return now + 0.02


def test_releasing_clutch_returns_to_aligning_and_freezes():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(packet(joints=[1.0] * N_JOINTS, clutch=False, seq=3), ZEROS, now=t + 0.02)
    assert result.state is State.ALIGNING
    assert result.targets is not None


def test_watchdog_moves_to_hold_after_timeout():
    gate = SafetyGate(make_config(watchdog_ms=200))
    t = _engage(gate)
    # 190ms: 아직 아님
    assert gate.step(None, ZEROS, now=t + 0.190).state is State.ENGAGED
    # 210ms: HOLD
    result = gate.step(None, ZEROS, now=t + 0.210)
    assert result.state is State.HOLD
    assert result.flags & Flag.WATCHDOG
    assert result.torque is True
    assert "watchdog" in (result.reason or "").lower()


def test_hold_does_not_recover_when_packets_resume():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.step(None, ZEROS, now=t + 0.5)
    assert gate.state is State.HOLD
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=10), ZEROS, now=t + 0.6)
    assert result.state is State.HOLD


def test_hold_never_falls_back_to_disconnected():
    """클라이언트가 완전히 사라져도 토크를 유지해야 한다 (스펙 §5.1)."""
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(None, ZEROS, now=t + 60.0)
    assert result.state is State.HOLD
    assert result.torque is True


def test_reset_command_returns_to_aligning():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.step(None, ZEROS, now=t + 0.5)
    assert gate.state is State.HOLD
    result = gate.step(packet(cmd=Cmd.RESET, seq=20), [5.0] * N_JOINTS, now=t + 0.6)
    assert result.state is State.ALIGNING
    assert result.reason is None
    # 리셋 후에는 현재 실제 자세를 기준으로 다시 잡는다
    assert result.targets == pytest.approx([5.0] * N_JOINTS)


def test_reset_is_ignored_outside_hold():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    result = gate.step(packet(cmd=Cmd.RESET, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.state is State.ENGAGED


def test_hold_targets_stay_frozen_even_if_actual_drifts():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    frozen = gate.step(None, ZEROS, now=t + 0.5).targets
    later = gate.step(None, [40.0] * N_JOINTS, now=t + 1.0).targets
    assert later == pytest.approx(frozen)


def test_force_hold_can_be_triggered_from_outside():
    """서보 통신 실패처럼 게이트가 알 수 없는 사유로도 HOLD 를 걸 수 있어야 한다."""
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.force_hold("motor communication failure")
    result = gate.step(packet(joints=ZEROS, clutch=True, seq=9), ZEROS, now=t + 0.02)
    assert result.state is State.HOLD
    assert result.reason == "motor communication failure"


def test_force_hold_still_requires_reset_to_clear():
    gate = SafetyGate(make_config())
    t = _engage(gate)
    gate.force_hold("motor communication failure")
    gate.step(packet(clutch=True, seq=9), ZEROS, now=t + 0.02)
    result = gate.step(packet(cmd=Cmd.RESET, seq=10), ZEROS, now=t + 0.04)
    assert result.state is State.ALIGNING
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_safety_states.py -v
```

기대: `ModuleNotFoundError: No module named 'workbench.safety'`.

- [ ] **Step 3: `workbench/safety.py`를 구현한다**

```python
"""안전 게이트 — 이 프로젝트에서 사고를 막는 로직 전부가 여기 있다.

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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_safety_states.py -v
```

기대: 15개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add safety gate state machine with watchdog and clutch edge detection"
```

---

### Task 4: 안전 게이트 — 클램프 3종

**Files:**
- Modify: `workbench/safety.py` (`_follow` 메서드를 실제 클램프로 교체)
- Test: `tests/test_safety_clamps.py`

**Interfaces:**
- Consumes: Task 3의 `SafetyGate`, `SafetyResult`
- Produces: 인터페이스 변경 없음. `_follow`의 동작만 바뀌고 `flags`에 `SPEED_CLAMPED` / `JOINT_LIMITED` / `FOLLOW_ERROR`가 실린다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_safety_clamps.py`:

```python
import pytest

from common.protocol import N_JOINTS, Cmd, ControlPacket, Flag, State
from tests.test_safety_states import ZEROS, make_config, packet
from workbench.safety import SafetyGate


def engage(gate, actual=None, now=0.0):
    a = actual if actual is not None else ZEROS
    gate.step(packet(joints=tuple(a)), a, now=now)
    gate.step(packet(joints=tuple(a), clutch=True, seq=2), a, now=now + 0.02)
    assert gate.state is State.ENGAGED
    return now + 0.02


def test_speed_clamp_limits_a_large_jump():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    far = [30.0] * N_JOINTS
    result = gate.step(packet(joints=far, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets == pytest.approx([1.5] * N_JOINTS)
    assert result.flags & Flag.SPEED_CLAMPED


def test_speed_clamp_ramps_over_multiple_frames():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    far = [30.0] * N_JOINTS
    seen = []
    for i in range(4):
        r = gate.step(packet(joints=far, clutch=True, seq=3 + i), ZEROS, now=t + 0.02 * (i + 1))
        seen.append(r.targets[0])
    assert seen == pytest.approx([1.5, 3.0, 4.5, 6.0])


def test_small_moves_are_not_clamped():
    gate = SafetyGate(make_config(max_step_deg=1.5))
    t = engage(gate)
    near = [0.5] * N_JOINTS
    result = gate.step(packet(joints=near, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets == pytest.approx(near)
    assert not (result.flags & Flag.SPEED_CLAMPED)


def test_joint_limit_clamps_target():
    cfg = make_config(max_step_deg=100.0)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 50.0
    result = gate.step(packet(joints=over, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets[0] == pytest.approx(10.0)
    assert result.flags & Flag.JOINT_LIMITED


def test_joint_limit_applies_before_speed_clamp():
    """한계 밖 목표를 향해 속도 제한만큼만 나아가야 한다."""
    cfg = make_config(max_step_deg=1.5)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 50.0
    result = gate.step(packet(joints=over, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets[0] == pytest.approx(1.5)
    assert result.flags & Flag.JOINT_LIMITED
    assert result.flags & Flag.SPEED_CLAMPED


# 추종 오차는 '지난 프레임에 쓴 명령각'과 '지금 실제각'을 비교한다.
# 따라서 명령을 처음 보낸 프레임에는 아직 오차가 없고, 그 다음 프레임부터 나타난다.
# 아래 테스트들이 한 스텝씩 더 진행하는 이유가 이것이다.
FAR = [40.0] * N_JOINTS


def test_follow_error_warns_but_does_not_hold_immediately():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    stuck = [0.0] * N_JOINTS  # 실제각은 0 에서 멈춰 있다 (팔이 걸림)

    first = gate.step(packet(joints=FAR, clutch=True, seq=3), stuck, now=t + 0.10)
    assert not (first.flags & Flag.FOLLOW_ERROR)  # 명령을 막 보낸 프레임

    second = gate.step(packet(joints=FAR, clutch=True, seq=4), stuck, now=t + 0.12)
    assert second.state is State.ENGAGED
    assert second.flags & Flag.FOLLOW_ERROR


def test_follow_error_holds_after_sustained_period():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    stuck = [0.0] * N_JOINTS

    gate.step(packet(joints=FAR, clutch=True, seq=3), stuck, now=t + 0.10)   # 명령 전달
    gate.step(packet(joints=FAR, clutch=True, seq=4), stuck, now=t + 0.12)   # 오차 감지 시작
    result = gate.step(packet(joints=FAR, clutch=True, seq=5), stuck, now=t + 0.70)  # 580ms 지속
    assert result.state is State.HOLD
    assert "follow" in (result.reason or "").lower()


def test_follow_error_timer_resets_when_error_clears():
    gate = SafetyGate(make_config(follow_error_deg=15.0, follow_error_hold_ms=500, max_step_deg=100.0))
    t = engage(gate)
    caught_up = list(FAR)

    gate.step(packet(joints=FAR, clutch=True, seq=3), ZEROS, now=t + 0.10)
    gate.step(packet(joints=FAR, clutch=True, seq=4), ZEROS, now=t + 0.12)  # 오차 타이머 시작
    # 팔이 따라잡았다 -> 타이머가 초기화되어야 한다
    gate.step(packet(joints=FAR, clutch=True, seq=5), caught_up, now=t + 0.30)
    result = gate.step(packet(joints=FAR, clutch=True, seq=6), caught_up, now=t + 0.90)
    assert result.state is State.ENGAGED
    assert not (result.flags & Flag.FOLLOW_ERROR)


def test_targets_never_exceed_joint_limits_over_a_long_run():
    cfg = make_config(max_step_deg=1.5)
    cfg.joint_limits["left_shoulder_pan"] = (-10.0, 10.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    over = [0.0] * N_JOINTS
    over[0] = 1000.0
    for i in range(200):
        r = gate.step(packet(joints=over, clutch=True, seq=3 + i), ZEROS, now=t + 0.02 * (i + 1))
        if r.state is not State.ENGAGED:
            break
        assert r.targets[0] <= 10.0 + 1e-6
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_safety_clamps.py -v
```

기대: `test_speed_clamp_limits_a_large_jump` 등이 FAIL — Task 3의 `_follow`가 목표를 그대로 통과시키므로 `targets == [30.0]*12`가 나온다.

- [ ] **Step 3: `_follow`를 클램프 구현으로 교체하고 상태를 추가한다**

`workbench/safety.py`의 `__init__`에 추종 오차 타이머를 추가한다:

```python
        self._reason: str | None = None
        self._follow_error_since: float | None = None
```

`_enter_aligning`에도 초기화를 추가한다:

```python
    def _enter_aligning(self, actual: list[float]) -> None:
        self._state = State.ALIGNING
        self._applied = list(actual)
        self._reason = None
        self._follow_error_since = None
        # 리셋 직후 클러치가 눌린 채라면 상승 에지를 요구하기 위해 눌림으로 간주한다.
        self._prev_clutch = True
```

`_follow`를 통째로 교체한다:

```python
    def _follow(self, packet: ControlPacket, actual: list[float], now: float) -> int:
        """ENGAGED 에서 리더를 추종하되 세 가지 안전 클램프를 적용한다.

        1. 관절 한계  - 목표가 물리적으로 허용된 범위 안인가
        2. 속도 제한  - 한 프레임에 너무 멀리 가지 않는가
        3. 추종 오차  - 팔이 뭔가에 걸려 있지 않은가 (감시만, 값은 안 바꿈)

        순서가 중요하다. 먼저 관절 한계로 목표를 자르고, 그 목표를 향해
        속도 제한만큼만 나아간다. 반대로 하면 한계 밖으로 넘어갈 수 있다.
        """
        assert self._applied is not None
        cfg = self._cfg
        flags = 0

        # 3. 추종 오차 — 지난 프레임에 '쓴' 각도와 지금 실제각을 비교한다.
        #    목표를 갱신하기 전에 판정해야 의미가 있다.
        max_error = max(abs(self._applied[i] - actual[i]) for i in range(N_JOINTS))
        if max_error > cfg.follow_error_deg:
            flags |= Flag.FOLLOW_ERROR
            if self._follow_error_since is None:
                self._follow_error_since = now
            elif (now - self._follow_error_since) >= cfg.follow_error_hold_ms / 1000.0:
                self._to_hold("follow error - arm may be blocked", Flag.FOLLOW_ERROR)
                return flags
        else:
            self._follow_error_since = None

        targets: list[float] = []
        for i, name in enumerate(JOINT_NAMES):
            lo, hi = cfg.joint_limits[name]

            # 1. 관절 한계
            desired = packet.joints[i]
            limited = min(max(desired, lo), hi)
            if limited != desired:
                flags |= Flag.JOINT_LIMITED

            # 2. 속도 제한
            delta = limited - self._applied[i]
            step = min(max(delta, -cfg.max_step_deg), cfg.max_step_deg)
            if step != delta:
                flags |= Flag.SPEED_CLAMPED

            targets.append(self._applied[i] + step)

        self._applied = targets
        return flags
```

> `_to_hold`는 `SafetyResult`를 반환하지만 여기서는 상태 전이 부수효과만 쓴다. `step`이 마지막에 `self._result(flags)`를 다시 만들므로 HOLD 상태가 그대로 반영된다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_safety_states.py tests/test_safety_clamps.py -v
```

기대: 두 파일 24개 테스트 전부 PASS. **Task 3의 상태머신 테스트가 하나도 깨지지 않아야 한다.**

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add speed, joint limit and follow error clamps to safety gate"
```

---

### Task 5: 하드웨어 경계 + mock 팔

**Files:**
- Create: `common/devices.py`, `mock/fake_arms.py`
- Test: `tests/test_fake_arms.py`

**Interfaces:**
- Consumes: `common.protocol.N_JOINTS`
- Produces:
  - `common.devices`: `class FollowerArms(Protocol)` — `read_positions() -> list[float]`, `write_positions(angles: Sequence[float]) -> None`, `set_torque(enabled: bool) -> None`, `close() -> None`
  - `common.devices`: `class LeaderArms(Protocol)` — `read_positions() -> list[float]`, `close() -> None`
  - `common.devices`: `class Camera(Protocol)` — `read() -> np.ndarray | None`, `close() -> None`
  - `mock.fake_arms.FakeFollowerArms(initial: Sequence[float] | None = None, lag: float = 0.0, blocks: dict[int, float] | None = None)` — 위 Protocol 구현 + `torque` 속성
  - `mock.fake_arms.FakeLeaderArms(base: Sequence[float] | None = None, amplitude_deg: float = 20.0, period_s: float = 8.0, clock: Callable[[], float] = time.monotonic)` — `read_positions()`, `close()`, `motion_enabled: bool` 속성

`FakeLeaderArms.motion_enabled`는 기본 `False`이며, 이때 `base` 자세를 그대로 반환한다. mock 데모에서 리더와 팔로워가 같은 자세에서 시작해 **정렬 절차를 통과할 수 있게** 하기 위함이다. `True`로 켜면 관절별 위상차가 있는 사인파를 낸다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_fake_arms.py`:

```python
import pytest

from common.protocol import N_JOINTS
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms


def test_follower_starts_at_initial_pose():
    arms = FakeFollowerArms(initial=[3.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([3.0] * N_JOINTS)


def test_follower_defaults_to_zeros():
    assert FakeFollowerArms().read_positions() == pytest.approx([0.0] * N_JOINTS)


def test_follower_reaches_command_immediately_without_lag():
    arms = FakeFollowerArms()
    arms.write_positions([7.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([7.0] * N_JOINTS)


def test_follower_lags_toward_command():
    arms = FakeFollowerArms(lag=0.5)
    arms.write_positions([10.0] * N_JOINTS)
    assert arms.read_positions()[0] == pytest.approx(5.0)
    arms.write_positions([10.0] * N_JOINTS)
    assert arms.read_positions()[0] == pytest.approx(7.5)


def test_blocked_joint_cannot_pass_its_limit():
    """추종 오차 로직을 검증하기 위한 '팔이 걸림' 시뮬레이션."""
    arms = FakeFollowerArms(blocks={2: 5.0})
    arms.write_positions([50.0] * N_JOINTS)
    pos = arms.read_positions()
    assert pos[2] == pytest.approx(5.0)
    assert pos[1] == pytest.approx(50.0)


def test_torque_flag_is_tracked():
    arms = FakeFollowerArms()
    assert arms.torque is False
    arms.set_torque(True)
    assert arms.torque is True


def test_follower_rejects_wrong_joint_count():
    with pytest.raises(ValueError):
        FakeFollowerArms().write_positions([0.0] * 3)


def test_leader_is_static_until_motion_enabled():
    clock = iter([0.0, 1.0, 2.0, 3.0])
    arms = FakeLeaderArms(base=[2.0] * N_JOINTS, clock=lambda: next(clock))
    assert arms.read_positions() == pytest.approx([2.0] * N_JOINTS)
    assert arms.read_positions() == pytest.approx([2.0] * N_JOINTS)


def test_leader_moves_when_motion_enabled():
    t = [0.0]
    arms = FakeLeaderArms(base=[0.0] * N_JOINTS, amplitude_deg=20.0, period_s=8.0, clock=lambda: t[0])
    arms.motion_enabled = True
    first = arms.read_positions()
    t[0] = 2.0
    second = arms.read_positions()
    assert first != pytest.approx(second)
    assert all(abs(v) <= 20.0 + 1e-6 for v in second)


def test_leader_joints_are_out_of_phase():
    arms = FakeLeaderArms(base=[0.0] * N_JOINTS, clock=lambda: 1.0)
    arms.motion_enabled = True
    pos = arms.read_positions()
    assert len(set(round(v, 6) for v in pos)) > 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_fake_arms.py -v
```

기대: `ModuleNotFoundError: No module named 'mock.fake_arms'`.

- [ ] **Step 3: `common/devices.py`를 구현한다**

```python
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
```

- [ ] **Step 4: `mock/fake_arms.py`를 구현한다**

```python
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
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_fake_arms.py -v
```

기대: 10개 테스트 전부 PASS.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add device protocols and mock arm implementations"
```

---

### Task 6: mock 카메라

**Files:**
- Create: `mock/fake_cameras.py`
- Test: `tests/test_fake_cameras.py`

**Interfaces:**
- Consumes: `common.devices.Camera`
- Produces: `mock.fake_cameras.FakeCamera(cam_id: int, name: str, width: int, height: int, clock: Callable[[], float] = time.monotonic)` — `read() -> np.ndarray`, `close()`, 읽기 전용 속성 `frame_number: int`

프레임에 **프레임 번호와 시각을 큼직하게 그린다.** 화면에 표시된 번호로 영상 지연을 눈으로 확인할 수 있게 하기 위함이다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_fake_cameras.py`:

```python
import numpy as np

from mock.fake_cameras import FakeCamera


def test_frame_has_requested_shape_and_dtype():
    cam = FakeCamera(cam_id=0, name="front", width=320, height=240)
    frame = cam.read()
    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_frame_number_increments_per_read():
    cam = FakeCamera(cam_id=1, name="wrist_left", width=64, height=48)
    cam.read()
    assert cam.frame_number == 1
    cam.read()
    assert cam.frame_number == 2


def test_consecutive_frames_differ():
    """정지 화면이 아니어야 영상이 살아있는지 눈으로 판별할 수 있다."""
    t = [0.0]
    cam = FakeCamera(cam_id=0, name="front", width=160, height=120, clock=lambda: t[0])
    first = cam.read().copy()
    t[0] = 0.5
    second = cam.read()
    assert not np.array_equal(first, second)


def test_different_cameras_render_differently():
    a = FakeCamera(cam_id=0, name="front", width=160, height=120, clock=lambda: 0.0).read()
    b = FakeCamera(cam_id=1, name="wrist_left", width=160, height=120, clock=lambda: 0.0).read()
    assert not np.array_equal(a, b)


def test_frame_encodes_to_jpeg():
    import cv2

    cam = FakeCamera(cam_id=2, name="wrist_right", width=320, height=240)
    ok, buf = cv2.imencode(".jpg", cam.read(), [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    assert ok
    # 320x240 q80 은 대략 5~25KB 범위에 들어온다
    assert 1000 < len(buf) < 60000


def test_close_is_idempotent():
    cam = FakeCamera(cam_id=0, name="front", width=64, height=48)
    cam.close()
    cam.close()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_fake_cameras.py -v
```

기대: `ModuleNotFoundError: No module named 'mock.fake_cameras'`.

- [ ] **Step 3: `mock/fake_cameras.py`를 구현한다**

```python
"""합성 영상 생성기.

프레임 번호와 시각을 큼직하게 그려 넣는다. 화면에 뜬 번호가 멈춰 있으면
영상이 끊긴 것이고, 번호가 건너뛰면 프레임이 버려진 것이다. 실제 카메라로는
알 수 없는 것들을 눈으로 확인할 수 있게 하는 것이 목적이다.
"""

from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

# 카메라마다 배경색을 다르게 해서 화면에서 즉시 구분되게 한다 (BGR).
_BACKGROUNDS = [(60, 40, 40), (40, 60, 40), (40, 40, 60)]


class FakeCamera:
    def __init__(
        self,
        cam_id: int,
        name: str,
        width: int,
        height: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cam_id = cam_id
        self._name = name
        self._width = width
        self._height = height
        self._clock = clock
        self._frame_number = 0

    @property
    def frame_number(self) -> int:
        return self._frame_number

    def read(self) -> np.ndarray:
        self._frame_number += 1
        t = self._clock()

        frame = np.empty((self._height, self._width, 3), dtype=np.uint8)
        frame[:, :] = _BACKGROUNDS[self._cam_id % len(_BACKGROUNDS)]

        # 시각에 따라 도는 막대 - 영상이 살아있는지 한눈에 보인다
        cx, cy = self._width // 2, self._height // 2
        radius = min(cx, cy) - 6
        angle = 2.0 * np.pi * (t % 4.0) / 4.0
        end = (int(cx + radius * np.cos(angle)), int(cy + radius * np.sin(angle)))
        cv2.line(frame, (cx, cy), end, (200, 200, 200), 2)
        cv2.circle(frame, (cx, cy), radius, (90, 90, 90), 1)

        scale = self._width / 320.0
        cv2.putText(
            frame, self._name, (6, int(18 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (220, 220, 220), 1, cv2.LINE_AA,
        )
        cv2.putText(
            frame, f"#{self._frame_number}", (6, self._height - int(8 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), 1, cv2.LINE_AA,
        )
        cv2.putText(
            frame, f"{t:8.2f}s", (self._width - int(110 * scale), self._height - int(8 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45 * scale, (180, 220, 180), 1, cv2.LINE_AA,
        )
        return frame

    def close(self) -> None:
        pass
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_fake_cameras.py -v
```

기대: 6개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add synthetic mock cameras with frame counter overlay"
```

---

### Task 7: 소켓 헬퍼 (`recv_exactly`)

**Files:**
- Create: `common/netutil.py`
- Test: `tests/test_netutil.py`

**Interfaces:**
- Consumes: 없음
- Produces: `common.netutil.recv_exactly(sock, n: int) -> bytes | None` — 정확히 n바이트를 모아 반환. 연결이 끊기면 `None`.

TCP의 `recv(n)`은 n보다 **적게** 반환할 수 있다. 초보자가 가장 자주 틀리는 부분이므로 별도 함수로 분리해 테스트한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_netutil.py`:

```python
import pytest

from common.netutil import recv_exactly


class ChunkedSocket:
    """recv 가 요청보다 적게 돌려주는 상황을 재현하는 가짜 소켓."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, n):
        if not self._chunks:
            return b""  # 연결 종료
        chunk = self._chunks.pop(0)
        return chunk[:n]


def test_reassembles_data_split_across_many_recv_calls():
    sock = ChunkedSocket([b"ab", b"cd", b"ef"])
    assert recv_exactly(sock, 6) == b"abcdef"


def test_returns_none_when_connection_closes_early():
    sock = ChunkedSocket([b"ab"])
    assert recv_exactly(sock, 6) is None


def test_single_chunk():
    assert recv_exactly(ChunkedSocket([b"hello"]), 5) == b"hello"


def test_zero_length_returns_empty_bytes():
    assert recv_exactly(ChunkedSocket([]), 0) == b""


def test_does_not_over_read():
    sock = ChunkedSocket([b"abcdefghij"])
    assert recv_exactly(sock, 4) == b"abcd"


def test_negative_length_is_rejected():
    with pytest.raises(ValueError):
        recv_exactly(ChunkedSocket([]), -1)
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_netutil.py -v
```

기대: `ModuleNotFoundError: No module named 'common.netutil'`.

- [ ] **Step 3: `common/netutil.py`를 구현한다**

```python
"""소켓 헬퍼."""

from __future__ import annotations


def recv_exactly(sock, n: int) -> bytes | None:
    """소켓에서 정확히 n바이트를 모아 반환한다.

    TCP 의 ``recv(n)`` 은 요청한 것보다 적게 돌려줄 수 있다. 한 번만 호출하고
    다 받았다고 믿는 것이 TCP 프로그래밍에서 가장 흔한 버그다.

    Returns:
        n바이트. 도중에 연결이 끊기면 None.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return b""

    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_netutil.py -v
```

기대: 6개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add recv_exactly socket helper for TCP framing"
```

---

### Task 8: 영상 송신 (`camera_pub.py`)

**Files:**
- Create: `workbench/camera_pub.py`
- Test: `tests/test_camera_pub.py`

**Interfaces:**
- Consumes: `common.devices.Camera`, `common.protocol.VideoHeader`, `common.config.CameraConfig`
- Produces:
  - `CameraPublisher(camera: Camera, cam_id: int, fps: int, jpeg_quality: int, clock=time.monotonic)` — `start()`, `stop()`, `latest() -> tuple[bytes, float, int] | None` (jpeg, t_capture, seq), `capture_once() -> None`
  - `VideoServer(port: int, publishers: list[CameraPublisher], clock=time.monotonic)` — `start()`, `stop()`, 속성 `port` (0을 넘기면 OS가 고른 실제 포트)

**1슬롯 버퍼**: 퍼블리셔는 최신 프레임 1장만 들고 있다. 송신 스레드는 `latest()`가 새 `seq`를 낼 때만 보낸다. 전송이 밀리는 동안 생긴 프레임은 자동으로 버려지므로 영상이 뒤처지지 않는다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_camera_pub.py`:

```python
import socket
import time

import numpy as np
import pytest

from common.netutil import recv_exactly
from common.protocol import VIDEO_HEADER_SIZE, VideoHeader
from mock.fake_cameras import FakeCamera
from workbench.camera_pub import CameraPublisher, VideoServer


def make_publisher(cam_id=0, **kwargs):
    cam = FakeCamera(cam_id=cam_id, name=f"cam{cam_id}", width=64, height=48)
    return CameraPublisher(camera=cam, cam_id=cam_id, fps=15, jpeg_quality=80, **kwargs)


def test_latest_is_none_before_any_capture():
    assert make_publisher().latest() is None


def test_capture_once_produces_a_jpeg():
    pub = make_publisher()
    pub.capture_once()
    got = pub.latest()
    assert got is not None
    jpeg, t_capture, seq = got
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI 마커
    assert seq == 1
    assert t_capture > 0


def test_sequence_increments_and_old_frame_is_discarded():
    """1슬롯 버퍼: 새로 찍으면 이전 프레임은 사라진다."""
    pub = make_publisher()
    pub.capture_once()
    first_jpeg, _, first_seq = pub.latest()
    pub.capture_once()
    second_jpeg, _, second_seq = pub.latest()
    assert second_seq == first_seq + 1
    assert second_jpeg != first_jpeg


def test_video_server_streams_frames_to_a_connected_client():
    pub = make_publisher(cam_id=1)
    pub.capture_once()
    server = VideoServer(port=0, publishers=[pub])
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
            assert header_bytes is not None
            header = VideoHeader.unpack(header_bytes)
            assert header is not None
            assert header.cam_id == 1
            assert header.length > 0
            payload = recv_exactly(sock, header.length)
            assert payload is not None
            assert len(payload) == header.length
            assert payload[:2] == b"\xff\xd8"
    finally:
        server.stop()


def test_video_server_multiplexes_all_cameras_on_one_connection():
    pubs = [make_publisher(cam_id=i) for i in range(3)]
    for p in pubs:
        p.start()
    server = VideoServer(port=0, publishers=pubs)
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            seen = set()
            deadline = time.monotonic() + 10.0
            while len(seen) < 3 and time.monotonic() < deadline:
                hb = recv_exactly(sock, VIDEO_HEADER_SIZE)
                assert hb is not None
                h = VideoHeader.unpack(hb)
                assert h is not None
                assert recv_exactly(sock, h.length) is not None
                seen.add(h.cam_id)
            assert seen == {0, 1, 2}
    finally:
        server.stop()
        for p in pubs:
            p.stop()


def test_new_connection_replaces_the_old_one():
    pub = make_publisher()
    pub.start()
    server = VideoServer(port=0, publishers=[pub])
    server.start()
    try:
        first = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
        first.settimeout(5.0)
        assert recv_exactly(first, VIDEO_HEADER_SIZE) is not None

        second = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
        second.settimeout(5.0)
        assert recv_exactly(second, VIDEO_HEADER_SIZE) is not None

        # 새 연결이 붙었으므로 이전 연결은 곧 닫힌다
        deadline = time.monotonic() + 5.0
        closed = False
        while time.monotonic() < deadline:
            try:
                if first.recv(4096) == b"":
                    closed = True
                    break
            except OSError:
                closed = True
                break
        assert closed
        first.close()
        second.close()
    finally:
        server.stop()
        pub.stop()


def test_capture_thread_runs_at_roughly_the_requested_fps():
    cam = FakeCamera(cam_id=0, name="c", width=64, height=48)
    pub = CameraPublisher(camera=cam, cam_id=0, fps=30, jpeg_quality=60)
    pub.start()
    try:
        time.sleep(0.5)
        got = pub.latest()
        assert got is not None
        _, _, seq = got
        # 0.5초 * 30fps = 약 15장. 타이밍 여유를 크게 준다.
        assert 5 <= seq <= 40
    finally:
        pub.stop()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_camera_pub.py -v
```

기대: `ModuleNotFoundError: No module named 'workbench.camera_pub'`.

- [ ] **Step 3: `workbench/camera_pub.py`를 구현한다**

```python
"""카메라 캡처 → JPEG 인코딩 → TCP 송신.

핵심은 **1슬롯 버퍼**다. 퍼블리셔는 최신 프레임 1장만 들고 있고, 송신이
밀리는 동안 찍힌 프레임은 그냥 덮어써서 버린다. 큐에 쌓으면 영상이 점점
뒤처져 결국 수 초 전 화면을 보며 조종하게 된다. 화질이 아니라 최신성을
지키는 쪽을 택한다 (스펙 §5.6).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import cv2

from common.devices import Camera
from common.protocol import VideoHeader

log = logging.getLogger(__name__)


class CameraPublisher:
    """카메라 1대를 지정한 fps 로 캡처해 JPEG 1장을 항상 최신으로 유지한다."""

    def __init__(
        self,
        camera: Camera,
        cam_id: int,
        fps: int,
        jpeg_quality: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self._camera = camera
        self._cam_id = cam_id
        self._interval = 1.0 / fps
        self._encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
        self._clock = clock

        self._lock = threading.Lock()
        self._latest: tuple[bytes, float, int] | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def cam_id(self) -> int:
        return self._cam_id

    def capture_once(self) -> None:
        """한 장 찍어 최신 슬롯에 넣는다. 테스트와 캡처 스레드가 함께 쓴다."""
        frame = self._camera.read()
        if frame is None:
            log.warning("camera %d: capture failed, skipping frame", self._cam_id)
            return
        ok, buf = cv2.imencode(".jpg", frame, self._encode_params)
        if not ok:
            log.warning("camera %d: jpeg encode failed, skipping frame", self._cam_id)
            return
        with self._lock:
            self._seq += 1
            self._latest = (buf.tobytes(), self._clock(), self._seq)

    def latest(self) -> tuple[bytes, float, int] | None:
        """(jpeg, t_capture, seq) 또는 아직 한 장도 없으면 None."""
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"cam{self._cam_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._camera.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            try:
                self.capture_once()
            except Exception:
                log.exception("camera %d: capture loop error", self._cam_id)
            elapsed = self._clock() - started
            self._stop.wait(max(0.0, self._interval - elapsed))


class VideoServer:
    """TCP 로 모든 카메라의 최신 프레임을 한 연결에 다중화해 내보낸다.

    한 번에 한 클라이언트만 받는다. 새 연결이 오면 기존 연결을 끊는데,
    재접속 시 유령 연결이 남아 대역폭을 갉아먹는 것을 막기 위함이다.
    """

    def __init__(
        self,
        port: int,
        publishers: list[CameraPublisher],
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.002,
    ) -> None:
        self._requested_port = port
        self._publishers = publishers
        self._clock = clock
        self._poll_interval = poll_interval

        self._listener: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None
        self._send_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = port

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", self._requested_port))
        listener.listen(1)
        listener.settimeout(0.5)
        self._listener = listener
        self.port = listener.getsockname()[1]

        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="video-accept", daemon=True)
        self._send_thread = threading.Thread(target=self._send_loop, name="video-send", daemon=True)
        self._accept_thread.start()
        self._send_thread.start()
        log.info("video server listening on port %d", self.port)

    def stop(self) -> None:
        self._stop.set()
        for t in (self._accept_thread, self._send_thread):
            if t is not None:
                t.join(timeout=2.0)
        self._accept_thread = None
        self._send_thread = None
        self._close_conn()
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            log.info("video client connected from %s", addr)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._close_conn()  # 새 연결이 기존 연결을 대체한다
            with self._conn_lock:
                self._conn = conn

    def _send_loop(self) -> None:
        last_sent: dict[int, int] = {}
        while not self._stop.is_set():
            with self._conn_lock:
                conn = self._conn
            if conn is None:
                self._stop.wait(0.05)
                continue

            sent_any = False
            for pub in self._publishers:
                latest = pub.latest()
                if latest is None:
                    continue
                jpeg, t_capture, seq = latest
                if last_sent.get(pub.cam_id) == seq:
                    continue  # 아직 새 프레임이 없다
                header = VideoHeader(
                    cam_id=pub.cam_id, seq=seq, t_capture=t_capture, length=len(jpeg)
                )
                try:
                    conn.sendall(header.pack() + jpeg)
                except OSError:
                    log.info("video client disconnected")
                    self._close_conn()
                    last_sent.clear()
                    break
                last_sent[pub.cam_id] = seq
                sent_any = True

            if not sent_any:
                self._stop.wait(self._poll_interval)

    def _close_conn(self) -> None:
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_camera_pub.py -v
```

기대: 7개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add camera publisher with one-slot buffer and multiplexed video server"
```

---

### Task 9: 영상 수신 (`video_recv.py`)

**Files:**
- Create: `home/video_recv.py`
- Test: `tests/test_video_recv.py`

**Interfaces:**
- Consumes: `common.netutil.recv_exactly`, `common.protocol.VideoHeader`, Task 8의 `VideoServer`
- Produces:
  - `VideoClient(host: str, port: int, reconnect_delay: float = 1.0, clock=time.monotonic)` — `start()`, `stop()`, `latest(cam_id: int) -> tuple[np.ndarray, float, int] | None` (frame, t_capture, seq), 속성 `connected: bool`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_video_recv.py`:

```python
import time

import numpy as np
import pytest

from home.video_recv import VideoClient
from mock.fake_cameras import FakeCamera
from workbench.camera_pub import CameraPublisher, VideoServer


def wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def running_server():
    pubs = [
        CameraPublisher(
            camera=FakeCamera(cam_id=i, name=f"cam{i}", width=64, height=48),
            cam_id=i,
            fps=30,
            jpeg_quality=70,
        )
        for i in range(3)
    ]
    for p in pubs:
        p.start()
    server = VideoServer(port=0, publishers=pubs)
    server.start()
    yield server
    server.stop()
    for p in pubs:
        p.stop()


def test_receives_and_decodes_frames_from_all_cameras(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: all(client.latest(i) is not None for i in range(3)))
        frame, t_capture, seq = client.latest(0)
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (48, 64, 3)
        assert t_capture > 0
        assert seq >= 1
    finally:
        client.stop()


def test_reports_connected_state(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    assert client.connected is False
    client.start()
    try:
        assert wait_until(lambda: client.connected)
    finally:
        client.stop()
    assert client.connected is False


def test_frames_keep_advancing(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: client.latest(1) is not None)
        first_seq = client.latest(1)[2]
        assert wait_until(lambda: client.latest(1)[2] > first_seq, timeout=5.0)
    finally:
        client.stop()


def test_reconnects_when_the_server_comes_back(running_server):
    """서버가 죽었다 살아나면 스스로 다시 붙어야 한다."""
    port = running_server.port
    client = VideoClient(host="127.0.0.1", port=port, reconnect_delay=0.1)
    client.start()
    try:
        assert wait_until(lambda: client.connected)
        running_server.stop()
        assert wait_until(lambda: not client.connected, timeout=5.0)

        pub = CameraPublisher(
            camera=FakeCamera(cam_id=0, name="revived", width=64, height=48),
            cam_id=0,
            fps=30,
            jpeg_quality=70,
        )
        pub.start()
        revived = VideoServer(port=port, publishers=[pub])
        revived.start()
        try:
            assert wait_until(lambda: client.connected, timeout=10.0)
        finally:
            revived.stop()
            pub.stop()
    finally:
        client.stop()


def test_start_is_idempotent(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    client.start()
    try:
        assert wait_until(lambda: client.connected)
    finally:
        client.stop()


def test_latest_returns_none_for_unknown_camera(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: client.latest(0) is not None)
        assert client.latest(99) is None
    finally:
        client.stop()


def test_connecting_to_nothing_does_not_crash():
    client = VideoClient(host="127.0.0.1", port=1, reconnect_delay=0.05)
    client.start()
    try:
        time.sleep(0.3)
        assert client.connected is False
    finally:
        client.stop()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_video_recv.py -v
```

기대: `ModuleNotFoundError: No module named 'home.video_recv'`.

- [ ] **Step 3: `home/video_recv.py`를 구현한다**

```python
"""영상 수신 — TCP 연결, 프레이밍 해제, JPEG 디코딩, 자동 재접속.

연결은 **집에서 개시한다.** 데이터는 작업대 → 집 단방향으로 흐르지만
포트포워딩이 작업대에만 설정되어 있기 때문이다 (스펙 §4.5).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import cv2
import numpy as np

from common.netutil import recv_exactly
from common.protocol import VIDEO_HEADER_SIZE, VideoHeader

log = logging.getLogger(__name__)


class VideoClient:
    def __init__(
        self,
        host: str,
        port: int,
        reconnect_delay: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self._port = port
        self._reconnect_delay = reconnect_delay
        self._clock = clock

        self._lock = threading.Lock()
        self._frames: dict[int, tuple[np.ndarray, float, int]] = {}
        self._connected = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def latest(self, cam_id: int) -> tuple[np.ndarray, float, int] | None:
        """(frame, t_capture, seq) 또는 아직 받은 게 없으면 None."""
        with self._lock:
            return self._frames.get(cam_id)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="video-recv", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._connected = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with socket.create_connection((self._host, self._port), timeout=3.0) as sock:
                    sock.settimeout(2.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._lock:
                        self._connected = True
                    log.info("video connected to %s:%d", self._host, self._port)
                    self._receive_forever(sock)
            except OSError as exc:
                log.debug("video connection failed: %s", exc)
            finally:
                with self._lock:
                    self._connected = False
            self._stop.wait(self._reconnect_delay)

    def _receive_forever(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
            except socket.timeout:
                continue
            if header_bytes is None:
                return  # 연결 종료

            header = VideoHeader.unpack(header_bytes)
            if header is None:
                log.warning("video: bad frame header, dropping connection")
                return  # 스트림이 어긋났다. 재접속이 유일한 복구 방법이다.
            if not (0 < header.length <= 8 * 1024 * 1024):
                log.warning("video: implausible frame length %d, dropping connection", header.length)
                return

            payload = recv_exactly(sock, header.length)
            if payload is None:
                return

            frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                log.warning("video: jpeg decode failed for cam %d", header.cam_id)
                continue

            with self._lock:
                self._frames[header.cam_id] = (frame, header.t_capture, header.seq)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_video_recv.py -v
```

기대: 7개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add video client with framing, decoding and auto-reconnect"
```

---

### Task 10: 제어 서버 (`server.py`)

**Files:**
- Create: `workbench/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `common.config.WorkbenchConfig`, `common.devices.FollowerArms`, `workbench.safety.SafetyGate`, `workbench.camera_pub.VideoServer`, `common.protocol` 전부
- Produces:
  - `TeleopServer(cfg: WorkbenchConfig, follower: FollowerArms, video: VideoServer | None = None, clock=time.monotonic)` — `start()`, `stop()`, 속성 `control_port: int`(0을 넘기면 실제 포트), `state: State`
  - `build_server(cfg: WorkbenchConfig) -> tuple[TeleopServer, list[CameraPublisher]]` — mock/실물을 설정에 따라 조립
  - `main(argv: list[str] | None = None) -> int` — `--config` 인자 처리, `python -m workbench.server`의 진입점

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_server.py`:

```python
import socket
import time

import pytest

from common.protocol import (
    N_JOINTS,
    Cmd,
    ControlPacket,
    Flag,
    State,
    TelemetryPacket,
)
from mock.fake_arms import FakeFollowerArms
from tests.test_safety_states import make_config
from workbench.server import TeleopServer

ZEROS = tuple([0.0] * N_JOINTS)


class Client:
    """테스트용 UDP 제어 클라이언트."""

    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2.0)
        self.addr = ("127.0.0.1", port)
        self.seq = 0

    def send(self, joints=ZEROS, clutch=False, cmd=Cmd.NONE):
        self.seq += 1
        pkt = ControlPacket(
            seq=self.seq, t_send=time.time(), clutch=clutch, cmd=cmd, joints=tuple(joints)
        )
        self.sock.sendto(pkt.pack(), self.addr)
        return self.seq

    def recv(self):
        data, _ = self.sock.recvfrom(4096)
        return TelemetryPacket.unpack(data)

    def exchange(self, **kwargs):
        seq = self.send(**kwargs)
        telem = self.recv()
        assert telem is not None
        assert telem.seq_echo == seq
        return telem

    def close(self):
        self.sock.close()


@pytest.fixture
def server_and_arms():
    from common.config import WorkbenchConfig

    arms = FakeFollowerArms()
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    yield server, arms
    server.stop()


def test_echoes_sequence_and_reports_state(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        telem = client.exchange()
        assert telem.state is State.ALIGNING
    finally:
        client.close()


def test_ignores_packets_with_bad_magic(server_and_arms):
    server, _ = server_and_arms
    raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw.settimeout(0.5)
    try:
        bad = bytearray(ControlPacket(1, 1.0, False, Cmd.NONE, ZEROS).pack())
        bad[0:4] = b"XXXX"
        raw.sendto(bytes(bad), ("127.0.0.1", server.control_port))
        with pytest.raises(socket.timeout):
            raw.recvfrom(4096)
    finally:
        raw.close()


def test_turns_torque_on_when_a_client_appears(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        assert arms.torque is True
    finally:
        client.close()


def test_full_engage_flow_drives_the_arms(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()                       # ALIGNING
        telem = client.exchange(clutch=True)    # 정렬됨(둘 다 0) + 상승 에지
        assert telem.state is State.ENGAGED

        target = [5.0] * N_JOINTS
        for _ in range(10):
            telem = client.exchange(joints=target, clutch=True)
        # 속도 클램프 1.5도/프레임이므로 5도에 도달했어야 한다
        assert telem.joints == pytest.approx(target, abs=0.2)
    finally:
        client.close()


def test_speed_clamp_flag_reaches_the_client(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        telem = client.exchange(joints=[60.0] * N_JOINTS, clutch=True)
        assert telem.flags & Flag.SPEED_CLAMPED
    finally:
        client.close()


def test_watchdog_holds_when_client_goes_silent(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        time.sleep(0.4)  # watchdog_ms=200
        telem = client.exchange(clutch=True)
        assert telem.state is State.HOLD
        assert telem.flags & Flag.WATCHDOG
    finally:
        client.close()


def test_reset_recovers_from_hold(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.exchange(clutch=True)
        time.sleep(0.4)
        assert client.exchange().state is State.HOLD
        telem = client.exchange(cmd=Cmd.RESET)
        assert telem.state is State.ALIGNING
    finally:
        client.close()


def test_out_of_order_packet_is_ignored(server_and_arms):
    server, _ = server_and_arms
    client = Client(server.control_port)
    try:
        client.exchange()
        client.seq = 1000
        client.exchange()
        # 낡은 시퀀스로 보내면 응답이 오지 않아야 한다
        stale = ControlPacket(seq=5, t_send=time.time(), clutch=False, cmd=Cmd.NONE, joints=ZEROS)
        client.sock.sendto(stale.pack(), client.addr)
        with pytest.raises(socket.timeout):
            client.sock.recvfrom(4096)
    finally:
        client.close()


def test_stop_disables_torque(server_and_arms):
    server, arms = server_and_arms
    client = Client(server.control_port)
    client.exchange()
    client.close()
    server.stop()
    assert arms.torque is False


class FlakyArms(FakeFollowerArms):
    """지정한 호출 횟수 이후 서보 통신이 실패하는 팔 (스펙 §9)."""

    def __init__(self, fail_after: int):
        super().__init__()
        self._reads = 0
        self._fail_after = fail_after

    def read_positions(self):
        self._reads += 1
        if self._reads > self._fail_after:
            raise OSError("serial bus read failed")
        return super().read_positions()


def test_motor_failure_holds_after_three_retries():
    from common.config import WorkbenchConfig

    arms = FlakyArms(fail_after=5)
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    client = Client(server.control_port)
    try:
        deadline = time.monotonic() + 3.0
        telem = None
        while time.monotonic() < deadline:
            client.send()
            try:
                telem = client.recv()
            except socket.timeout:
                continue
            if telem is not None and telem.state is State.HOLD:
                break
        assert telem is not None
        assert telem.state is State.HOLD
        assert telem.flags & Flag.MOTOR_ERROR
    finally:
        client.close()
        server.stop()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_server.py -v
```

기대: `ModuleNotFoundError: No module named 'workbench.server'`.

- [ ] **Step 3: `workbench/server.py`를 구현한다**

```python
"""작업대 서버 — 제어 루프와 조립.

제어 채널은 UDP 다. 낡은 관절각을 재전송받아봐야 쓸모가 없기 때문이다
(스펙 §3). 영상은 별도 TCP 연결로 나가므로 영상 혼잡이 제어를 밀어내지 않는다.

와이어에 싣는 시각은 time.time(), 내부 판정은 time.monotonic() 을 쓴다.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from typing import Callable

from common.config import WorkbenchConfig, load_workbench_config
from common.devices import FollowerArms
from common.protocol import (
    CONTROL_SIZE,
    N_JOINTS,
    ControlPacket,
    Flag,
    State,
    TelemetryPacket,
    is_newer,
)
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.safety import SafetyGate

log = logging.getLogger(__name__)

#: 제어 소켓 수신 타임아웃. 패킷이 없어도 이 주기로 워치독을 돌린다.
_RECV_TIMEOUT = 0.005

#: 서보 읽기를 몇 번까지 즉시 재시도할 것인가. 초과하면 통신 고장으로 본다.
_MOTOR_RETRIES = 3


class TeleopServer:
    def __init__(
        self,
        cfg: WorkbenchConfig,
        follower: FollowerArms,
        video: VideoServer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._follower = follower
        self._video = video
        self._clock = clock
        self._gate = SafetyGate(cfg.safety)

        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._torque_state: bool | None = None
        self._last_actual: list[float] = [0.0] * N_JOINTS
        self.control_port = cfg.control_port

    @property
    def state(self) -> State:
        return self._gate.state

    @property
    def video_port(self) -> int | None:
        return self._video.port if self._video is not None else None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self._cfg.control_port))
        sock.settimeout(_RECV_TIMEOUT)
        self._sock = sock
        self.control_port = sock.getsockname()[1]

        if self._video is not None:
            self._video.start()

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="control", daemon=True)
        self._thread.start()
        log.info("control server listening on UDP %d", self.control_port)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._video is not None:
            self._video.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            self._follower.set_torque(False)
        finally:
            self._follower.close()

    def _loop(self) -> None:
        assert self._sock is not None
        last_seq: int | None = None
        client_addr: tuple[str, int] | None = None

        while not self._stop.is_set():
            packet: ControlPacket | None = None
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                pass
            except OSError:
                break
            else:
                if len(data) == CONTROL_SIZE:
                    candidate = ControlPacket.unpack(data)
                    if candidate is None:
                        log.debug("rejected packet from %s (bad magic)", addr)
                    elif last_seq is not None and not is_newer(candidate.seq, last_seq):
                        log.debug("dropped stale packet seq=%d (last=%d)", candidate.seq, last_seq)
                    else:
                        last_seq = candidate.seq
                        packet = candidate
                        client_addr = addr
                else:
                    log.debug("rejected packet from %s (size %d)", addr, len(data))

            now = self._clock()
            actual, motor_failed = self._read_actual()
            extra_flags = 0
            if motor_failed:
                extra_flags |= Flag.MOTOR_ERROR
                self._gate.force_hold("motor communication failure")

            result = self._gate.step(packet, actual, now)

            if self._torque_state != result.torque:
                try:
                    self._follower.set_torque(result.torque)
                    self._torque_state = result.torque
                except Exception:
                    log.exception("follower set_torque failed")
                    extra_flags |= Flag.MOTOR_ERROR
                    self._gate.force_hold("motor communication failure")

            if result.targets is not None and not motor_failed:
                try:
                    self._follower.write_positions(result.targets)
                except Exception:
                    log.exception("follower write failed")
                    extra_flags |= Flag.MOTOR_ERROR
                    self._gate.force_hold("motor communication failure")

            if packet is not None and client_addr is not None:
                telemetry = TelemetryPacket(
                    seq_echo=packet.seq,
                    t_send=time.time(),
                    state=self._gate.state,
                    flags=result.flags | extra_flags,
                    joints=tuple(actual),
                )
                try:
                    self._sock.sendto(telemetry.pack(), client_addr)
                except OSError:
                    log.debug("telemetry send failed")

    def _read_actual(self) -> tuple[list[float], bool]:
        """팔로워의 실제각을 읽는다.

        서보 버스는 가끔 한 번씩 읽기에 실패한다. 순간적인 실패로 HOLD 를 걸면
        쓸 수 없으므로 즉시 재시도하고, 연속 3회 실패해야 진짜 고장으로 본다
        (스펙 §9).

        Returns:
            (관절각, 통신 실패 여부). 실패했으면 마지막으로 성공한 값을 돌려준다.
        """
        for _ in range(_MOTOR_RETRIES):
            try:
                actual = self._follower.read_positions()
            except Exception as exc:
                log.warning("follower read failed: %s", exc)
                continue
            self._last_actual = actual
            return actual, False
        return list(self._last_actual), True


def build_server(cfg: WorkbenchConfig) -> tuple[TeleopServer, list[CameraPublisher]]:
    """설정에 따라 mock/실물을 조립한다.

    1단계에서는 use_mock 이 반드시 true 여야 한다. 실물 어댑터는 2단계에서
    추가된다.
    """
    if not cfg.use_mock:
        raise NotImplementedError(
            "real hardware adapters land in stage 2; set use_mock: true in the config"
        )

    from mock.fake_arms import FakeFollowerArms
    from mock.fake_cameras import FakeCamera

    follower = FakeFollowerArms()
    publishers = [
        CameraPublisher(
            camera=FakeCamera(cam_id=c.id, name=c.name, width=c.width, height=c.height),
            cam_id=c.id,
            fps=c.fps,
            jpeg_quality=c.jpeg_quality,
        )
        for c in cfg.cameras
    ]
    video = VideoServer(port=cfg.video_port, publishers=publishers)
    return TeleopServer(cfg=cfg, follower=follower, video=video), publishers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SO-101 teleoperation workbench server")
    parser.add_argument("--config", default="config/workbench.yaml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = load_workbench_config(args.config)
    server, publishers = build_server(cfg)
    for pub in publishers:
        pub.start()
    server.start()

    print(f"control  UDP  {server.control_port}")
    print(f"video    TCP  {server.video_port}")
    print("Ctrl-C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
        for pub in publishers:
            pub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_server.py -v
```

기대: 10개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add UDP control server wiring safety gate to follower arms"
```

---

### Task 11: HUD (pygame)

**Files:**
- Create: `home/hud.py`
- Test: `tests/test_hud.py`

**Interfaces:**
- Consumes: `common.protocol` (`State`, `Flag`, `N_JOINTS`, `JOINT_NAMES`)
- Produces:
  - `HudInput(clutch: bool, reset: bool, quit: bool, toggle_motion: bool)`
  - `HudStats(rtt_ms: float | None, lost_packets: int, video_connected: bool, telemetry_age_ms: float | None)`
  - `ClutchTracker(reset_hold_s: float = 3.0)` — `on_key_down(key, now)`, `on_key_up(key, now)`, `poll(now) -> HudInput`, 속성 `clutch: bool`
  - `Hud(cam_ids: list[int], cam_names: dict[int, str], width: int = 1000, height: int = 620)` — `poll(now) -> HudInput`, `draw(frames, telemetry, leader_joints, stats, align_threshold_deg)`, `close()`

**핵심:** `ClutchTracker`는 pygame과 무관한 순수 로직이므로 창을 띄우지 않고 테스트한다. 클러치는 스페이스 **누르고 있는 동안**, 리셋은 R을 **3초 이상** 누르고 있을 때 한 번만 발생한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_hud.py`:

```python
import pygame
import pytest

from home.hud import ClutchTracker, HudInput


def test_clutch_is_false_initially():
    assert ClutchTracker().poll(now=0.0).clutch is False


def test_clutch_is_true_while_space_held():
    t = ClutchTracker()
    t.on_key_down(pygame.K_SPACE, now=0.0)
    assert t.poll(now=0.1).clutch is True
    assert t.poll(now=0.5).clutch is True


def test_clutch_clears_on_key_up():
    t = ClutchTracker()
    t.on_key_down(pygame.K_SPACE, now=0.0)
    t.on_key_up(pygame.K_SPACE, now=0.2)
    assert t.poll(now=0.3).clutch is False


def test_reset_requires_holding_r_for_three_seconds():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=1.0).reset is False
    assert t.poll(now=2.9).reset is False
    assert t.poll(now=3.1).reset is True


def test_reset_fires_only_once_per_hold():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=3.1).reset is True
    assert t.poll(now=3.2).reset is False
    assert t.poll(now=5.0).reset is False


def test_reset_can_fire_again_after_releasing_and_re_holding():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=3.1).reset is True
    t.on_key_up(pygame.K_r, now=3.5)
    t.on_key_down(pygame.K_r, now=4.0)
    assert t.poll(now=7.1).reset is True


def test_releasing_r_early_cancels_reset():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    t.on_key_up(pygame.K_r, now=1.0)
    assert t.poll(now=4.0).reset is False


def test_quit_is_reported_once():
    t = ClutchTracker()
    t.on_key_down(pygame.K_ESCAPE, now=0.0)
    assert t.poll(now=0.1).quit is True
    assert t.poll(now=0.2).quit is False


def test_toggle_motion_is_reported_once_per_press():
    t = ClutchTracker()
    t.on_key_down(pygame.K_m, now=0.0)
    assert t.poll(now=0.1).toggle_motion is True
    assert t.poll(now=0.2).toggle_motion is False
    t.on_key_up(pygame.K_m, now=0.3)
    t.on_key_down(pygame.K_m, now=0.4)
    assert t.poll(now=0.5).toggle_motion is True


def test_unrelated_keys_do_nothing():
    t = ClutchTracker()
    t.on_key_down(pygame.K_z, now=0.0)
    assert t.poll(now=0.1) == HudInput(clutch=False, reset=False, quit=False, toggle_motion=False)
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_hud.py -v
```

기대: `ModuleNotFoundError: No module named 'home.hud'`.

- [ ] **Step 3: `home/hud.py`를 구현한다**

```python
"""조종자 화면과 키 입력.

OpenCV 가 아니라 pygame 을 쓰는 이유가 두 가지 있다.

1. 이 환경의 opencv 는 headless 빌드라 imshow 가 아예 없다.
2. 더 근본적으로, cv2.waitKey 는 키를 **뗀** 것을 감지하지 못한다.
   클러치는 '누르고 있는 동안'이라는 의미이므로 KEYUP 이 필수다.

키 배치:
  SPACE (누르고 있기)  클러치 - 이걸 놓으면 팔로워가 즉시 그 자리에 정지
  R     (3초 길게)      HOLD 해제 요청
  M                     mock 리더의 움직임 토글 (1단계 데모용)
  ESC                   종료
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pygame

from common.protocol import JOINT_NAMES, N_JOINTS, Flag, State

# 색 (RGB)
_BG = (18, 18, 22)
_FG = (225, 225, 230)
_DIM = (120, 120, 130)
_GREEN = (60, 200, 110)
_AMBER = (230, 170, 60)
_RED = (225, 70, 70)
_GREY = (110, 110, 120)

_STATE_COLOR = {
    State.DISCONNECTED: _GREY,
    State.ALIGNING: _AMBER,
    State.ENGAGED: _GREEN,
    State.HOLD: _RED,
    State.FAULT: _RED,
}

_FLAG_LABELS = [
    (Flag.SPEED_CLAMPED, "SPEED"),
    (Flag.JOINT_LIMITED, "LIMIT"),
    (Flag.FOLLOW_ERROR, "FOLLOW"),
    (Flag.WATCHDOG, "WDOG"),
    (Flag.MOTOR_ERROR, "MOTOR"),
]


@dataclass(frozen=True)
class HudInput:
    clutch: bool
    reset: bool
    quit: bool
    toggle_motion: bool


@dataclass(frozen=True)
class HudStats:
    rtt_ms: float | None
    lost_packets: int
    video_connected: bool
    telemetry_age_ms: float | None


class ClutchTracker:
    """키 상태를 클러치·리셋·종료 신호로 바꾼다. pygame 창 없이 테스트 가능하다."""

    def __init__(self, reset_hold_s: float = 3.0) -> None:
        self._reset_hold_s = reset_hold_s
        self._space_down = False
        self._r_down_at: float | None = None
        self._reset_fired = False
        self._quit_pending = False
        self._motion_pending = False

    @property
    def clutch(self) -> bool:
        return self._space_down

    def on_key_down(self, key: int, now: float) -> None:
        if key == pygame.K_SPACE:
            self._space_down = True
        elif key == pygame.K_r:
            if self._r_down_at is None:
                self._r_down_at = now
                self._reset_fired = False
        elif key == pygame.K_ESCAPE:
            self._quit_pending = True
        elif key == pygame.K_m:
            self._motion_pending = True

    def on_key_up(self, key: int, now: float) -> None:
        if key == pygame.K_SPACE:
            self._space_down = False
        elif key == pygame.K_r:
            self._r_down_at = None
            self._reset_fired = False

    def poll(self, now: float) -> HudInput:
        reset = False
        if self._r_down_at is not None and not self._reset_fired:
            if (now - self._r_down_at) >= self._reset_hold_s:
                reset = True
                self._reset_fired = True

        quit_now, self._quit_pending = self._quit_pending, False
        motion, self._motion_pending = self._motion_pending, False
        return HudInput(clutch=self._space_down, reset=reset, quit=quit_now, toggle_motion=motion)

    def reset_progress(self, now: float) -> float:
        """R 키를 얼마나 눌렀는지 0.0~1.0. 화면에 진행 막대를 그리는 용도."""
        if self._r_down_at is None or self._reset_fired:
            return 0.0
        return min(1.0, (now - self._r_down_at) / self._reset_hold_s)


class Hud:
    def __init__(
        self,
        cam_ids: list[int],
        cam_names: dict[int, str],
        width: int = 1000,
        height: int = 620,
    ) -> None:
        pygame.init()
        pygame.display.set_caption("SO-101 Remote Teleoperation")
        self._screen = pygame.display.set_mode((width, height))
        self._font = pygame.font.SysFont("consolas", 15)
        self._big = pygame.font.SysFont("consolas", 26, bold=True)
        self._small = pygame.font.SysFont("consolas", 12)
        self._cam_ids = cam_ids
        self._cam_names = cam_names
        self._width = width
        self._height = height
        self._tracker = ClutchTracker()

    def poll(self, now: float) -> HudInput:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return HudInput(clutch=False, reset=False, quit=True, toggle_motion=False)
            if event.type == pygame.KEYDOWN:
                self._tracker.on_key_down(event.key, now)
            elif event.type == pygame.KEYUP:
                self._tracker.on_key_up(event.key, now)
        return self._tracker.poll(now)

    def draw(
        self,
        frames: dict[int, np.ndarray | None],
        telemetry,
        leader_joints: list[float] | None,
        stats: HudStats,
        align_threshold_deg: float,
        now: float = 0.0,
    ) -> None:
        self._screen.fill(_BG)

        state = telemetry.state if telemetry is not None else State.DISCONNECTED
        stale = stats.telemetry_age_ms is not None and stats.telemetry_age_ms > 300.0
        border = _RED if (stale or state in (State.HOLD, State.FAULT)) else _STATE_COLOR[state]

        self._draw_videos(frames)
        self._draw_joint_bars(telemetry, leader_joints, align_threshold_deg)
        self._draw_status(state, telemetry, stats, stale, now)

        pygame.draw.rect(self._screen, border, self._screen.get_rect(), width=6)
        pygame.display.flip()

    # ------------------------------------------------------------------ #

    def _draw_videos(self, frames: dict[int, np.ndarray | None]) -> None:
        pane_w, pane_h = 320, 240
        for i, cam_id in enumerate(self._cam_ids):
            x = 16 + i * (pane_w + 8)
            y = 16
            frame = frames.get(cam_id)
            if frame is None:
                pygame.draw.rect(self._screen, (35, 35, 40), (x, y, pane_w, pane_h))
                self._screen.blit(
                    self._font.render(f"cam {cam_id}: no signal", True, _DIM), (x + 10, y + 110)
                )
            else:
                # OpenCV 는 BGR, pygame 은 RGB. 축도 (h,w) -> (w,h) 로 뒤집는다.
                rgb = frame[:, :, ::-1]
                surface = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
                surface = pygame.transform.scale(surface, (pane_w, pane_h))
                self._screen.blit(surface, (x, y))
            label = self._cam_names.get(cam_id, f"cam{cam_id}")
            self._screen.blit(self._small.render(label, True, _FG), (x + 4, y + pane_h + 3))

    def _draw_joint_bars(
        self, telemetry, leader_joints: list[float] | None, threshold: float
    ) -> None:
        top = 290
        self._screen.blit(
            self._font.render("alignment error (leader vs follower)", True, _DIM), (16, top - 20)
        )
        if telemetry is None or leader_joints is None:
            self._screen.blit(self._font.render("waiting for telemetry...", True, _DIM), (16, top))
            return

        bar_w, bar_h, gap = 74, 16, 4
        for i in range(N_JOINTS):
            col, row = i % 6, i // 6
            x = 16 + col * (bar_w + 46)
            y = top + row * (bar_h + gap + 16)
            error = abs(leader_joints[i] - telemetry.joints[i])
            ok = error < threshold
            filled = int(min(1.0, error / max(threshold * 4.0, 1e-6)) * bar_w)

            pygame.draw.rect(self._screen, (45, 45, 52), (x, y, bar_w, bar_h))
            pygame.draw.rect(self._screen, _GREEN if ok else _AMBER, (x, y, filled or 2, bar_h))
            name = JOINT_NAMES[i].replace("left_", "L.").replace("right_", "R.")
            self._screen.blit(self._small.render(name, True, _DIM), (x, y - 13))
            self._screen.blit(
                self._small.render(f"{error:5.1f}", True, _FG), (x + bar_w + 4, y + 1)
            )

    def _draw_status(self, state, telemetry, stats: HudStats, stale: bool, now: float) -> None:
        y = 470
        self._screen.blit(self._big.render(state.name, True, _STATE_COLOR[state]), (16, y))

        rtt = f"{stats.rtt_ms:5.1f} ms" if stats.rtt_ms is not None else "  --  "
        lines = [
            f"RTT      {rtt}",
            f"lost     {stats.lost_packets}",
            f"video    {'connected' if stats.video_connected else 'DISCONNECTED'}",
        ]
        for i, line in enumerate(lines):
            self._screen.blit(self._font.render(line, True, _FG), (240, y + i * 19))

        flags = telemetry.flags if telemetry is not None else 0
        for i, (flag, label) in enumerate(_FLAG_LABELS):
            color = _RED if flags & flag else (55, 55, 62)
            x = 470 + i * 66
            pygame.draw.rect(self._screen, color, (x, y + 2, 60, 22))
            self._screen.blit(self._small.render(label, True, _FG), (x + 6, y + 8))

        if stale:
            self._screen.blit(
                self._big.render("LINK LOST", True, _RED), (self._width - 190, y)
            )

        progress = self._tracker.reset_progress(now)
        if progress > 0.0:
            pygame.draw.rect(self._screen, (55, 55, 62), (16, y + 40, 300, 12))
            pygame.draw.rect(self._screen, _AMBER, (16, y + 40, int(300 * progress), 12))
            self._screen.blit(
                self._small.render("hold R to reset...", True, _AMBER), (322, y + 40)
            )

        help_text = "SPACE hold=clutch   R hold 3s=reset   M=mock motion   ESC=quit"
        self._screen.blit(self._small.render(help_text, True, _DIM), (16, self._height - 22))

    def close(self) -> None:
        pygame.quit()
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_hud.py -v
```

기대: 10개 테스트 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add pygame HUD with clutch tracking and alignment bars"
```

---

### Task 12: 클라이언트 조립 + 종단 통합 테스트

**Files:**
- Create: `home/client.py`
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: 전부
- Produces:
  - `ControlLink(host: str, port: int, clock=time.monotonic)` — `start()`, `stop()`, `send(joints, clutch, reset) -> None`, `latest_telemetry() -> tuple[TelemetryPacket, float] | None`, 속성 `rtt_ms: float | None`, `lost_packets: int`
  - `main(argv: list[str] | None = None) -> int` — `python -m home.client`의 진입점

`ControlLink`는 HUD 없이 동작하므로 통합 테스트에서 창을 띄우지 않고 종단 흐름을 검증할 수 있다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_integration.py`:

```python
import time

import pytest

from common.config import WorkbenchConfig
from common.protocol import N_JOINTS, Flag, State
from home.client import ControlLink
from home.video_recv import VideoClient
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms
from mock.fake_cameras import FakeCamera
from tests.test_safety_states import make_config
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.server import TeleopServer


def wait_until(predicate, timeout=10.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def stack():
    """서버 + 클라이언트 전체를 localhost 에 띄운다."""
    arms = FakeFollowerArms()
    pubs = [
        CameraPublisher(
            camera=FakeCamera(cam_id=i, name=f"cam{i}", width=64, height=48),
            cam_id=i,
            fps=15,
            jpeg_quality=70,
        )
        for i in range(3)
    ]
    video_server = VideoServer(port=0, publishers=pubs)
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=video_server)
    for p in pubs:
        p.start()
    server.start()

    link = ControlLink(host="127.0.0.1", port=server.control_port)
    link.start()
    video_client = VideoClient(host="127.0.0.1", port=video_server.port, reconnect_delay=0.1)
    video_client.start()

    yield server, arms, link, video_client

    link.stop()
    video_client.stop()
    server.stop()
    for p in pubs:
        p.stop()


def _telemetry_state(link):
    got = link.latest_telemetry()
    return got[0].state if got else None


def test_end_to_end_reaches_aligning(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))


def _send_and_check(link, leader, expected, clutch=False):
    link.send(joints=leader.read_positions(), clutch=clutch, reset=False)
    return _telemetry_state(link) is expected


def test_end_to_end_engages_and_follows(stack):
    _, arms, link, _ = stack
    leader = FakeLeaderArms()

    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    # 클러치 상승 에지를 만들기 위해 놓았다 누른다
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    time.sleep(0.05)
    assert wait_until(lambda: _send_and_check(link, leader, State.ENGAGED, clutch=True))

    leader.motion_enabled = True
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        link.send(joints=leader.read_positions(), clutch=True, reset=False)
        time.sleep(1 / 60)

    # 팔로워가 실제로 원점에서 벗어나 움직였어야 한다
    assert max(abs(v) for v in arms.read_positions()) > 1.0
    assert _telemetry_state(link) is State.ENGAGED


def test_rtt_is_measured(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    for _ in range(30):
        link.send(joints=leader.read_positions(), clutch=False, reset=False)
        time.sleep(1 / 60)
    assert wait_until(lambda: link.rtt_ms is not None)
    assert 0.0 <= link.rtt_ms < 100.0  # localhost


def test_video_arrives_on_all_three_cameras(stack):
    _, _, _, video = stack
    assert wait_until(lambda: all(video.latest(i) is not None for i in range(3)))
    assert video.connected is True


def test_watchdog_holds_when_client_stops_sending(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    time.sleep(0.05)
    assert wait_until(lambda: _send_and_check(link, leader, State.ENGAGED, clutch=True))

    time.sleep(0.4)  # watchdog_ms=200
    link.send(joints=leader.read_positions(), clutch=True, reset=False)
    assert wait_until(lambda: _telemetry_state(link) is State.HOLD)
    got = link.latest_telemetry()
    assert got[0].flags & Flag.WATCHDOG


def test_reset_recovers_the_stack(stack):
    _, _, link, _ = stack
    leader = FakeLeaderArms()
    assert wait_until(lambda: _send_and_check(link, leader, State.ALIGNING))
    time.sleep(0.4)
    link.send(joints=leader.read_positions(), clutch=False, reset=False)
    assert wait_until(lambda: _telemetry_state(link) is State.HOLD)

    link.send(joints=leader.read_positions(), clutch=False, reset=True)
    assert wait_until(lambda: _telemetry_state(link) is State.ALIGNING)


def test_blocked_arm_triggers_follow_error_hold():
    """팔이 걸리면 HOLD 로 가야 한다 (물리 사고 방어)."""
    arms = FakeFollowerArms(blocks={2: 1.0})
    cfg = WorkbenchConfig(
        use_mock=True,
        control_port=0,
        video_port=0,
        cameras=[],
        safety=make_config(
            follow_error_deg=5.0, follow_error_hold_ms=200, max_step_deg=50.0
        ),
    )
    server = TeleopServer(cfg=cfg, follower=arms, video=None)
    server.start()
    link = ControlLink(host="127.0.0.1", port=server.control_port)
    link.start()
    try:
        zeros = [0.0] * N_JOINTS

        def pump(clutch):
            link.send(joints=zeros, clutch=clutch, reset=False)
            return _telemetry_state(link)

        assert wait_until(lambda: pump(False) is State.ALIGNING)
        assert wait_until(lambda: pump(True) is State.ENGAGED)

        far = [0.0] * N_JOINTS
        far[2] = 60.0  # 2번 관절은 1.0 도에서 막혀 있다
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _telemetry_state(link) is not State.HOLD:
            link.send(joints=far, clutch=True, reset=False)
            time.sleep(1 / 60)
        assert _telemetry_state(link) is State.HOLD
    finally:
        link.stop()
        server.stop()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_integration.py -v
```

기대: `ModuleNotFoundError: No module named 'home.client'`.

- [ ] **Step 3: `home/client.py`를 구현한다**

```python
"""집 클라이언트 — 리더 읽기, 제어 송신, 텔레메트리 수신, 화면 조립.

RTT 는 seq 반향으로 잰다. 두 시각 모두 이쪽 시계에서 재므로 서버와 시계가
어긋나 있어도 정확하다 (스펙 §4.8). t_send 로 편도 지연을 계산하면 틀린다.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from typing import Callable

from common.config import HomeConfig, load_home_config
from common.protocol import (
    TELEMETRY_SIZE,
    Cmd,
    ControlPacket,
    N_JOINTS,
    TelemetryPacket,
    is_newer,
)

log = logging.getLogger(__name__)

_RECV_TIMEOUT = 0.05
#: RTT 계산용 송신 시각 보관 개수. 60Hz 에서 약 4초분.
_SENT_HISTORY = 256


class ControlLink:
    """UDP 제어 채널. HUD 없이도 동작하므로 통합 테스트에서 그대로 쓸 수 있다."""

    def __init__(
        self, host: str, port: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._addr = (host, port)
        self._clock = clock
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._seq = 0
        self._sent_at: dict[int, float] = {}
        self._telemetry: tuple[TelemetryPacket, float] | None = None
        self._rtt_ms: float | None = None
        self._last_echo: int | None = None
        self._lost = 0

    @property
    def rtt_ms(self) -> float | None:
        with self._lock:
            return self._rtt_ms

    @property
    def lost_packets(self) -> int:
        with self._lock:
            return self._lost

    def latest_telemetry(self) -> tuple[TelemetryPacket, float] | None:
        """(패킷, 수신 시각(monotonic)) 또는 아직 없으면 None."""
        with self._lock:
            return self._telemetry

    def start(self) -> None:
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(_RECV_TIMEOUT)
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._recv_loop, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send(self, joints, clutch: bool, reset: bool) -> None:
        if self._sock is None:
            raise RuntimeError("ControlLink.start() must be called first")
        if len(joints) != N_JOINTS:
            raise ValueError(f"joints must have {N_JOINTS} elements, got {len(joints)}")

        with self._lock:
            self._seq += 1
            seq = self._seq
            self._sent_at[seq] = self._clock()
            if len(self._sent_at) > _SENT_HISTORY:
                for old in sorted(self._sent_at)[: len(self._sent_at) - _SENT_HISTORY]:
                    del self._sent_at[old]

        packet = ControlPacket(
            seq=seq,
            t_send=time.time(),
            clutch=clutch,
            cmd=Cmd.RESET if reset else Cmd.NONE,
            joints=tuple(float(v) for v in joints),
        )
        try:
            self._sock.sendto(packet.pack(), self._addr)
        except OSError as exc:
            log.debug("control send failed: %s", exc)

    def _recv_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) != TELEMETRY_SIZE:
                continue
            telemetry = TelemetryPacket.unpack(data)
            if telemetry is None:
                continue

            now = self._clock()
            with self._lock:
                sent_at = self._sent_at.pop(telemetry.seq_echo, None)
                if sent_at is not None:
                    self._rtt_ms = (now - sent_at) * 1000.0
                if self._last_echo is not None and is_newer(telemetry.seq_echo, self._last_echo):
                    self._lost += telemetry.seq_echo - self._last_echo - 1
                self._last_echo = telemetry.seq_echo
                self._telemetry = (telemetry, now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SO-101 teleoperation home client")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg: HomeConfig = load_home_config(args.config)

    if not cfg.use_mock:
        raise NotImplementedError(
            "real leader arms land in stage 2; set use_mock: true in the config"
        )

    from home.hud import Hud, HudStats
    from home.video_recv import VideoClient
    from mock.fake_arms import FakeLeaderArms

    leader = FakeLeaderArms()
    link = ControlLink(host=cfg.server_host, port=cfg.control_port)
    video = VideoClient(host=cfg.server_host, port=cfg.video_port)
    cam_ids = [0, 1, 2]
    hud = Hud(cam_ids=cam_ids, cam_names={0: "front", 1: "wrist_left", 2: "wrist_right"})

    link.start()
    video.start()

    send_interval = 1.0 / 60.0
    next_send = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            action = hud.poll(now)
            if action.quit:
                break
            if action.toggle_motion:
                leader.motion_enabled = not leader.motion_enabled
                log.info("mock leader motion: %s", leader.motion_enabled)

            leader_joints = leader.read_positions()
            if now >= next_send:
                link.send(joints=leader_joints, clutch=action.clutch, reset=action.reset)
                next_send = now + send_interval

            got = link.latest_telemetry()
            telemetry = got[0] if got else None
            age_ms = (now - got[1]) * 1000.0 if got else None

            frames = {}
            for cam_id in cam_ids:
                latest = video.latest(cam_id)
                frames[cam_id] = latest[0] if latest else None

            hud.draw(
                frames=frames,
                telemetry=telemetry,
                leader_joints=leader_joints,
                stats=HudStats(
                    rtt_ms=link.rtt_ms,
                    lost_packets=link.lost_packets,
                    video_connected=video.connected,
                    telemetry_age_ms=age_ms,
                ),
                align_threshold_deg=3.0,
                now=now,
            )
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        hud.close()
        video.stop()
        link.stop()
        leader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통합 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_integration.py -v
```

기대: 7개 테스트 전부 PASS.

- [ ] **Step 5: 전체 테스트를 돌린다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ -v
```

기대: 테스트 파일 12개, 총 109개 테스트가 전부 PASS. 실패가 하나라도 있으면 다음 단계로 넘어가지 않는다.

- [ ] **Step 6: 손으로 직접 돌려본다 (1단계 통과 기준)**

터미널 두 개를 연다.

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m workbench.server --config config/workbench.yaml
```

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m home.client --config config/home.yaml
```

다음을 눈으로 확인한다:

1. 창이 뜨고 카메라 3개 영상에 프레임 번호가 계속 올라간다
2. 상태가 `ALIGNING`(주황)이고 관절 막대 12개가 전부 초록이다 (mock 리더·팔로워가 둘 다 0도)
3. `RTT`가 표시되고 localhost 기준 1ms 미만이다
4. **스페이스를 누르면** `ENGAGED`(초록)로 바뀐다
5. **M을 누르면** mock 리더가 사인파로 움직이기 시작하고, 관절 막대가 흔들리며 팔로워가 따라간다
6. **스페이스를 놓으면** 즉시 `ALIGNING`으로 돌아간다
7. **서버 터미널을 Ctrl-C로 죽이면** 0.3초 안에 화면 테두리가 빨개지고 `LINK LOST`가 뜬다
8. 서버를 다시 켜면 `HOLD`(빨강)에 머물러 있고, **저절로 움직이지 않는다**
9. **R을 3초간 누르면** 진행 막대가 차오르고 `ALIGNING`으로 복귀한다

- [ ] **Step 7: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add home client with control link and end-to-end integration tests"
```

---

## 1단계 완료 기준 (스펙 §11)

| 기준 | 확인 방법 |
|---|---|
| 단위 테스트 전부 통과 | `pytest tests/ -v` |
| 왕복 지연 <5ms | HUD의 RTT 표시 (localhost) |
| 워치독·클램프·정렬 시나리오 수동 확인 | Task 12 Step 6의 항목 1~9 |
| 24시간 연속 실행 후 크래시·메모리 증가 없음 | 서버·클라이언트를 켜둔 채 다음 날 확인 |

24시간 시험은 마지막에 한 번만 하면 된다. 그 사이에 2단계 계획(실물 팔 어댑터, 캘리브레이션, COM 포트 조회)을 작성할 수 있다.

---

## 2단계로 넘길 항목

- `workbench/follower_arms.py` — lerobot `SO101Follower` 2대 래핑, `FollowerArms` Protocol 구현
- `home/leader_arms.py` — lerobot `SO101Leader` 2대 래핑, `LeaderArms` Protocol 구현
- `common/serial_ports.py` — USB 시리얼 번호로 COM 포트 조회 (COM 번호가 뒤바뀌어 좌우 팔이 섞이는 사고 방지)
- 실제 카메라 어댑터 (`cv2.VideoCapture` 래핑, `Camera` Protocol 구현)
- `config/*.yaml`에 `arms:` 섹션 추가
- **`joint_limits` 실측 확정** — 현재 값은 mock 전용 임시값이다
- `follow_error_deg` / `follow_error_hold_ms` / `max_step_deg` 실기 감각 조정
- 캘리브레이션 절차 문서화 (팔 4대 각각)
