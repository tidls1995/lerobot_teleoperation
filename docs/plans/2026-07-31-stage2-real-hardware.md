# 2단계(실물 하드웨어) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1단계에서 완성한 원격 텔레오퍼레이션 소프트웨어를 실제 SO-101 팔 4대(리더 2 + 팔로워 2)와 실제 USB 카메라 3대에 연결해, 개발 PC 한 대에서 localhost로 실제 팔이 실제 팔을 따라오게 만든다.

**Architecture:** 1단계에서 `common/devices.py`의 Protocol 3개(`LeaderArms`/`FollowerArms`/`Camera`)로 하드웨어 경계를 이미 격리해 두었다. 이번 단계는 **그 Protocol의 실물 구현을 추가하는 것뿐**이며, 프로토콜·안전 게이트·네트워크·HUD는 한 줄도 바뀌지 않는다. lerobot 0.5.2의 `SOLeader`/`SOFollower`를 감싸고, COM 포트는 USB 시리얼 번호로 고정한다.

**Tech Stack:** lerobot 0.5.2 (`SOLeader`, `SOFollower`, `FeetechMotorsBus`), pyserial 3.5 (`serial.tools.list_ports`), opencv-python-headless (`cv2.VideoCapture`), pytest

**설계 스펙:** [`docs/specs/2026-07-31-remote-teleoperation-design.md`](../specs/2026-07-31-remote-teleoperation-design.md)
**1단계 계획:** [`docs/plans/2026-07-31-stage1-mock-teleoperation.md`](2026-07-31-stage1-mock-teleoperation.md)

**범위:** 스펙 §11의 **2단계만**. 개발 PC 한 대에 팔 4대와 카메라 3대를 모두 연결하고 localhost로 실행한다. 2대의 PC로 나누는 것(3단계)과 인터넷(4단계)은 다음 계획에서 다룬다.

**전제:** 리더 2대 + 팔로워 2대 + USB 카메라 3대가 물리적으로 준비되어 있다.

---

## Global Constraints

- Python 인터프리터는 항상 `C:/Users/flash/miniconda3/envs/lerobot/python.exe`.
- 작업 디렉터리는 `C:/Users/flash/Desktop/lerobot/remote teleoperation` (**경로에 공백 있음 — 모든 명령에서 따옴표 필수**).
- **1단계에서 검증한 코드는 최소한으로만 건드린다.** 이것이 "문제가 생기면 하드웨어가 원인"이라는 1단계의 성과를 지키는 방법이다. 허용되는 변경은 아래 4개뿐이며, 그 밖의 1단계 파일은 손대지 않는다:
  - `common/config.py` — `arms:` 섹션과 그리퍼 안전값 추가 (Task 3)
  - `workbench/safety.py` — 속도 클램프를 관절별로 (한 줄, Task 4)
  - `workbench/server.py` — `build_server()` 의 실물 분기와 `start()` 의 장치 연결 훅 (Task 7). **제어 루프(`_loop`)는 건드리지 않는다**
  - `workbench/camera_pub.py`, `home/client.py` — 장치 열기 훅과 조립 분기만 (Task 7)
- **`common/protocol.py`, `home/hud.py`, `home/video_recv.py` 는 이번 단계에서 한 줄도 바뀌지 않는다.** 와이어 포맷이 그대로여야 3단계에서 두 PC 로 나눌 때 1단계·2단계 코드가 서로 통신할 수 있다.
- **관절 단위 (스펙 §4.3):** 인덱스 0~4, 6~10 = **도(degree)**. 인덱스 **5, 11 = 그리퍼, 퍼센트 0~100**. 두 단위가 한 배열에 섞여 있다.
- **관절 순서 (스펙 §4.3):** `left_*` 6개 뒤에 `right_*` 6개. lerobot 모터 이름은 접두사 없는 `shoulder_pan`/`shoulder_lift`/`elbow_flex`/`wrist_flex`/`wrist_roll`/`gripper`이며 `JOINT_NAMES`의 접미사와 정확히 일치한다.
- **lerobot 설정값 고정:** `use_degrees=True`, `max_relative_target=None`(클램프의 단일 출처는 `safety.py`), `disable_torque_on_disconnect=True`, `cameras={}`(카메라는 우리가 직접 다룬다).
- **캘리브레이션은 서버 안에서 돌리지 않는다.** lerobot의 `calibrate()`는 `input()`으로 사람에게 팔을 움직이라고 요구하는 대화형 절차다. `lerobot-calibrate` CLI로 미리 만든 파일만 읽는다.
- 안전 기본값(스펙 §13): 정렬 3.0, 속도 클램프 1.5/프레임, 추종오차 15.0/500ms, 서버 워치독 200ms, 클라이언트 워치독 300ms. **관절 한계와 그리퍼 관련 값은 이 단계에서 실측해 확정한다.**
- 커밋 메시지는 영문 Conventional Commits.
- **하드웨어를 처음 움직이는 모든 단계는 사람이 손을 전원 스위치에 올려놓은 상태에서 실행한다.**

---

## 파일 구조

| 파일 | 상태 | 책임 |
|---|---|---|
| `common/serial_ports.py` | **신규** | USB 시리얼 번호 → COM 포트 조회. 좌우 팔이 뒤바뀌는 사고 방지 |
| `common/joints.py` | **신규** | lerobot 의 관절 dict ↔ 우리 12칸 배열 변환. 양쪽에서 쓴다 |
| `workbench/follower_arms.py` | **신규** | `SOFollower` 2대를 `FollowerArms` Protocol 로 감싼다 |
| `home/leader_arms.py` | **신규** | `SOLeader` 2대를 `LeaderArms` Protocol 로 감싼다 |
| `workbench/usb_camera.py` | **신규** | `cv2.VideoCapture` 를 `Camera` Protocol 로 감싼다 |
| `common/config.py` | 수정 | `arms:` 섹션 추가, 그리퍼 전용 안전값 추가 |
| `workbench/server.py` | 수정 | `build_server()` 의 실물 분기만 |
| `home/client.py` | 수정 | `main()` 의 실물 분기만 |
| `config/workbench.yaml`, `config/home.yaml` | 수정 | 실측값 반영 |
| `tools/probe_hardware.py` | **신규** | 팔·카메라 개별 점검용 진단 스크립트 |

신규 파일 6개는 모두 **하드웨어 경계 바깥에서 순수하게 테스트 가능한 부분**(`serial_ports`, `joints`)과 **실물이 있어야만 검증되는 부분**(나머지)으로 나뉜다. 전자는 단위 테스트로, 후자는 진단 스크립트와 수동 검증으로 확인한다.

---

### Task 1: 관절 배열 변환 (`common/joints.py`)

lerobot 은 `{"shoulder_pan.pos": 12.3, ...}` 형태의 dict 를, 우리 프로토콜은 12칸 배열을 쓴다. 이 변환은 **순수 함수**이므로 하드웨어 없이 100% 테스트한다. 여기가 틀리면 어깨 명령이 손목에 들어간다.

**Files:**
- Create: `common/joints.py`
- Test: `tests/test_joints.py`

**Interfaces:**
- Consumes: `common.protocol.JOINT_NAMES`, `N_JOINTS`
- Produces:
  - `ARM_SIDES: tuple[str, ...]` = `("left", "right")`
  - `MOTOR_NAMES: tuple[str, ...]` = `("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")`
  - `GRIPPER_INDICES: tuple[int, int]` = `(5, 11)`
  - `arm_slice(side: str) -> slice` — 그 팔이 차지하는 배열 구간
  - `require_both_sides(arms: dict[str, object]) -> None` — 좌우 둘 다 있는지 검증. 리더·팔로워 어댑터가 **함께 쓴다**
  - `to_arrays(left: dict[str, float], right: dict[str, float]) -> list[float]` — lerobot dict 2개 → 12칸 배열
  - `to_dicts(joints: Sequence[float]) -> tuple[dict[str, float], dict[str, float]]` — 12칸 배열 → lerobot dict 2개 (`.pos` 접미사 포함)
  - `class JointNameError(Exception)`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_joints.py`:

```python
import pytest

from common.joints import (
    ARM_SIDES,
    GRIPPER_INDICES,
    MOTOR_NAMES,
    JointNameError,
    arm_slice,
    to_arrays,
    to_dicts,
)
from common.protocol import JOINT_NAMES, N_JOINTS


def left_dict(base=0.0):
    return {f"{m}.pos": base + i for i, m in enumerate(MOTOR_NAMES)}


def right_dict(base=100.0):
    return {f"{m}.pos": base + i for i, m in enumerate(MOTOR_NAMES)}


def test_motor_names_match_joint_names_suffixes():
    """lerobot 모터 이름이 우리 JOINT_NAMES 의 접미사와 정확히 같아야 한다."""
    assert len(MOTOR_NAMES) == 6
    for i, side in enumerate(ARM_SIDES):
        for j, motor in enumerate(MOTOR_NAMES):
            assert JOINT_NAMES[i * 6 + j] == f"{side}_{motor}"


def test_gripper_indices_point_at_grippers():
    for idx in GRIPPER_INDICES:
        assert JOINT_NAMES[idx].endswith("gripper")
    assert GRIPPER_INDICES == (5, 11)


def test_arm_slice_covers_six_joints_each():
    assert arm_slice("left") == slice(0, 6)
    assert arm_slice("right") == slice(6, 12)


def test_arm_slice_rejects_unknown_side():
    with pytest.raises(JointNameError, match="middle"):
        arm_slice("middle")


def test_to_arrays_places_left_then_right():
    arr = to_arrays(left_dict(), right_dict())
    assert len(arr) == N_JOINTS
    assert arr[:6] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    assert arr[6:] == pytest.approx([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])


def test_to_arrays_accepts_keys_without_pos_suffix():
    left = {m: float(i) for i, m in enumerate(MOTOR_NAMES)}
    arr = to_arrays(left, right_dict())
    assert arr[:6] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])


def test_to_arrays_rejects_missing_motor():
    broken = left_dict()
    del broken["elbow_flex.pos"]
    with pytest.raises(JointNameError, match="elbow_flex"):
        to_arrays(broken, right_dict())


def test_to_arrays_rejects_unexpected_motor():
    broken = left_dict()
    broken["thumb.pos"] = 1.0
    with pytest.raises(JointNameError, match="thumb"):
        to_arrays(broken, right_dict())


def test_to_dicts_roundtrips_with_to_arrays():
    original = to_arrays(left_dict(), right_dict())
    left, right = to_dicts(original)
    assert to_arrays(left, right) == pytest.approx(original)


def test_to_dicts_uses_pos_suffix():
    left, right = to_dicts([0.0] * N_JOINTS)
    assert set(left) == {f"{m}.pos" for m in MOTOR_NAMES}
    assert set(right) == {f"{m}.pos" for m in MOTOR_NAMES}


def test_to_dicts_rejects_wrong_length():
    with pytest.raises(ValueError):
        to_dicts([0.0] * 5)


def test_require_both_sides_accepts_left_and_right():
    from common.joints import require_both_sides

    require_both_sides({"left": object(), "right": object()})


def test_require_both_sides_reports_the_missing_one():
    from common.joints import require_both_sides

    with pytest.raises(ValueError, match="right"):
        require_both_sides({"left": object()})
    with pytest.raises(ValueError, match="left"):
        require_both_sides({"right": object()})
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_joints.py -v
```

기대: `ModuleNotFoundError: No module named 'common.joints'`.

- [ ] **Step 3: `common/joints.py`를 구현한다**

```python
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
GRIPPER_INDICES: tuple[int, int] = tuple(
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_joints.py -v
```

기대: 13개 테스트 전부 PASS. `test_motor_names_match_joint_names_suffixes`가 특히 중요하다 — 1단계에서 정한 `JOINT_NAMES`와 lerobot 의 모터 이름이 실제로 맞물리는지 확인하는 테스트다.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add joint array conversion between lerobot dicts and wire format"
```

---

### Task 2: COM 포트 고정 (`common/serial_ports.py`)

Windows 의 COM 번호는 USB 를 다시 꽂거나 재부팅하면 뒤바뀐다. **좌우 팔이 뒤바뀌면 재앙**이므로 설정에는 시리얼 번호를 적고 실행 시점에 COM 포트를 조회한다.

**Files:**
- Create: `common/serial_ports.py`
- Test: `tests/test_serial_ports.py`

**Interfaces:**
- Consumes: 없음 (pyserial 만)
- Produces:
  - `class PortLookupError(Exception)`
  - `PortInfo(device: str, serial_number: str | None, description: str)` (frozen dataclass)
  - `list_serial_ports() -> list[PortInfo]` — 실제 장치 목록 (테스트에서는 주입으로 대체)
  - `find_port_by_serial(serial_number: str, ports: list[PortInfo] | None = None) -> str` — COM 포트 문자열
  - `describe_ports(ports: list[PortInfo] | None = None) -> str` — 사람이 읽는 목록 (오류 메시지·진단용)
  - `resolve_port_spec(serial_number: str | None, port: str | None, ports: list[PortInfo] | None = None) -> str` — 설정이 준 방식대로 포트를 정한다. 리더·팔로워 어댑터가 **함께 쓴다**

`resolve_port_spec` 이 `ArmConfig` 대신 필드 2개를 직접 받는 이유: 그러면 `serial_ports.py` 가 `config.py` 를 import 하지 않아도 되고, 이 모듈이 계속 독립적으로 테스트된다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_serial_ports.py`:

```python
import pytest

from common.serial_ports import (
    PortInfo,
    PortLookupError,
    describe_ports,
    find_port_by_serial,
    list_serial_ports,
)

PORTS = [
    PortInfo(device="COM3", serial_number="AB12CD34", description="USB Serial"),
    PortInfo(device="COM4", serial_number="EF56GH78", description="USB Serial"),
    PortInfo(device="COM5", serial_number=None, description="Bluetooth"),
]


def test_finds_port_by_serial_number():
    assert find_port_by_serial("EF56GH78", PORTS) == "COM4"


def test_serial_number_match_is_case_insensitive():
    assert find_port_by_serial("ef56gh78", PORTS) == "COM4"


def test_unknown_serial_raises_and_lists_what_was_found():
    with pytest.raises(PortLookupError) as exc:
        find_port_by_serial("NOPE", PORTS)
    message = str(exc.value)
    assert "NOPE" in message
    # 오류 메시지가 실제로 붙어 있는 장치를 알려줘야 사용자가 고칠 수 있다
    assert "AB12CD34" in message
    assert "COM3" in message


def test_duplicate_serial_numbers_are_rejected():
    dupes = [
        PortInfo(device="COM3", serial_number="SAME", description="USB Serial"),
        PortInfo(device="COM9", serial_number="SAME", description="USB Serial"),
    ]
    with pytest.raises(PortLookupError, match="more than one"):
        find_port_by_serial("SAME", dupes)


def test_ports_without_serial_numbers_are_ignored_not_matched():
    with pytest.raises(PortLookupError):
        find_port_by_serial("None", PORTS)


def test_empty_serial_number_is_rejected():
    with pytest.raises(PortLookupError, match="empty"):
        find_port_by_serial("", PORTS)


def test_describe_ports_lists_every_port():
    text = describe_ports(PORTS)
    for expected in ("COM3", "COM4", "COM5", "AB12CD34", "Bluetooth"):
        assert expected in text


def test_describe_ports_handles_no_ports():
    assert "no serial ports" in describe_ports([]).lower()


def test_list_serial_ports_returns_port_infos():
    """실제 장치가 없어도 호출 자체는 성공해야 한다 (빈 목록이면 빈 목록)."""
    ports = list_serial_ports()
    assert isinstance(ports, list)
    for p in ports:
        assert isinstance(p, PortInfo)
        assert isinstance(p.device, str)


def test_resolve_port_spec_prefers_explicit_port():
    assert resolve_port_spec(serial_number=None, port="COM12", ports=PORTS) == "COM12"


def test_resolve_port_spec_looks_up_serial_number():
    assert resolve_port_spec(serial_number="EF56GH78", port=None, ports=PORTS) == "COM4"


def test_resolve_port_spec_rejects_neither():
    with pytest.raises(PortLookupError, match="exactly one"):
        resolve_port_spec(serial_number=None, port=None, ports=PORTS)


def test_resolve_port_spec_rejects_both():
    with pytest.raises(PortLookupError, match="exactly one"):
        resolve_port_spec(serial_number="AB12CD34", port="COM3", ports=PORTS)
```

`import` 에 `resolve_port_spec` 을 추가한다.

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_serial_ports.py -v
```

기대: `ModuleNotFoundError: No module named 'common.serial_ports'`.

- [ ] **Step 3: `common/serial_ports.py`를 구현한다**

```python
"""USB 시리얼 번호로 COM 포트를 찾는다.

설정 파일에 COM 번호를 직접 적으면 안 된다. Windows 의 COM 번호는 USB 를 다시
꽂거나 재부팅하면 뒤바뀌고, 좌우 팔이 뒤바뀐 채로 조종을 시작하면 조종자가
왼쪽을 움직였는데 오른쪽 팔이 장비를 치는 상황이 된다 (스펙 §7.2).

최초에 어느 시리얼 번호가 어느 팔인지 알아내려면 `lerobot-find-port` 를 쓰거나,
`describe_ports()` 를 출력해 놓고 팔을 하나씩 뽑아 보면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports


class PortLookupError(Exception):
    """지정한 시리얼 번호를 가진 포트를 특정할 수 없다."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    serial_number: str | None
    description: str


def list_serial_ports() -> list[PortInfo]:
    """현재 붙어 있는 시리얼 포트 목록."""
    return [
        PortInfo(
            device=p.device,
            serial_number=p.serial_number,
            description=p.description or "",
        )
        for p in list_ports.comports()
    ]


def describe_ports(ports: list[PortInfo] | None = None) -> str:
    """사람이 읽을 수 있는 포트 목록. 오류 메시지와 진단 스크립트가 함께 쓴다."""
    ports = list_serial_ports() if ports is None else ports
    if not ports:
        return "no serial ports found"
    lines = [
        f"  {p.device:8s} serial={p.serial_number or '(none)':20s} {p.description}" for p in ports
    ]
    return "\n".join(lines)


def find_port_by_serial(serial_number: str, ports: list[PortInfo] | None = None) -> str:
    """시리얼 번호에 해당하는 COM 포트를 돌려준다.

    Raises:
        PortLookupError: 못 찾았거나, 같은 번호가 둘 이상일 때.
    """
    if not serial_number:
        raise PortLookupError("serial_number is empty; set it in the config file")

    ports = list_serial_ports() if ports is None else ports
    wanted = serial_number.strip().lower()
    matches = [p for p in ports if p.serial_number and p.serial_number.strip().lower() == wanted]

    if len(matches) > 1:
        devices = ", ".join(p.device for p in matches)
        raise PortLookupError(
            f"serial number {serial_number!r} matches more than one port ({devices}); "
            "cannot tell the arms apart"
        )
    if not matches:
        raise PortLookupError(
            f"no serial port with serial number {serial_number!r}. Ports found:\n"
            f"{describe_ports(ports)}"
        )
    return matches[0].device


def resolve_port_spec(
    serial_number: str | None,
    port: str | None,
    ports: list[PortInfo] | None = None,
) -> str:
    """설정이 준 방식대로 COM 포트를 정한다.

    ArmConfig 를 받지 않고 필드 2개를 직접 받는다. 그래야 이 모듈이 config.py 를
    import 하지 않고 독립적으로 테스트된다.

    Raises:
        PortLookupError: 둘 다 주거나 둘 다 안 줬을 때, 또는 조회에 실패했을 때.
    """
    if (serial_number is None) == (port is None):
        raise PortLookupError(
            "specify exactly one of serial_number or port "
            f"(got serial_number={serial_number!r}, port={port!r})"
        )
    if port is not None:
        return port
    assert serial_number is not None
    return find_port_by_serial(serial_number, ports)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_serial_ports.py -v
```

기대: 13개 테스트 전부 PASS.

- [ ] **Step 5: 실제로 붙어 있는 포트를 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -c "from common.serial_ports import describe_ports; print(describe_ports())"
```

기대: 팔 4대를 꽂았다면 COM 포트 4개가 시리얼 번호와 함께 나온다. **이 출력을 적어둔다** — Task 6에서 설정 파일에 넣을 값이다.

시리얼 번호가 `(none)`으로 나오는 어댑터가 있으면 그 팔은 시리얼 번호로 구분할 수 없다. 그 경우 Task 6에서 COM 포트를 직접 적고, 기동 시 사람이 좌우를 확인하는 절차로 대체한다.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: resolve COM ports by USB serial number instead of hardcoding"
```

---

### Task 3: 설정 스키마 확장 (`arms:` + 그리퍼 안전값)

**Files:**
- Modify: `common/config.py`
- Modify: `config/workbench.yaml`, `config/home.yaml`
- Test: `tests/test_config.py` (추가)

**Interfaces:**
- Consumes: `common.joints.ARM_SIDES`, `GRIPPER_INDICES`, `common.protocol.JOINT_NAMES`
- Produces:
  - `ArmConfig(side: str, serial_number: str | None, port: str | None, calibration_id: str)`
  - `SafetyConfig` 에 필드 2개 추가: `gripper_max_step: float`, `gripper_limits: tuple[float, float]`
  - `WorkbenchConfig` 에 필드 추가: `arms: dict[str, ArmConfig]`
  - `HomeConfig` 에 필드 추가: `arms: dict[str, ArmConfig]`
  - `SafetyConfig.max_step_for(index: int) -> float` — 그리퍼면 `gripper_max_step`, 아니면 `max_step_deg`

`serial_number` 와 `port` 중 정확히 하나만 지정해야 한다. 둘 다 비거나 둘 다 채우면 설정 오류다 — "어느 쪽이 이겼는지" 모르는 상태를 만들지 않기 위함이다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_config.py` 끝에 추가한다. 파일 앞부분의 `WORKBENCH_YAML` / `HOME_YAML` / `_write` / `limit_line` / `LIMIT_INDENT` 는 1단계에서 이미 정의되어 있다.

```python


# --- 2단계: arms 섹션과 그리퍼 전용 안전값 --------------------------------

ARMS_YAML = """arms:
  left:  { serial_number: "AB12CD34", calibration_id: "left" }
  right: { serial_number: "EF56GH78", calibration_id: "right" }
"""

GRIPPER_YAML = """  gripper_max_step: 4.0
  gripper_limits: [0.0, 100.0]
"""


def workbench_with_arms():
    """1단계 YAML 에 arms 섹션과 그리퍼 안전값을 얹는다."""
    text = WORKBENCH_YAML.replace("  joint_limits:", GRIPPER_YAML + "  joint_limits:")
    # 그리퍼 한계는 퍼센트 단위이므로 도 단위 기본값을 덮어쓴다
    for name in ("left_gripper", "right_gripper"):
        text = text.replace(limit_line(name), f"{LIMIT_INDENT}{name}: [0.0, 100.0]")
    return text + ARMS_YAML


def home_with_arms():
    return HOME_YAML + ARMS_YAML


def test_workbench_config_reads_arms(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    assert set(cfg.arms) == {"left", "right"}
    assert cfg.arms["left"].serial_number == "AB12CD34"
    assert cfg.arms["left"].port is None
    assert cfg.arms["right"].calibration_id == "right"


def test_home_config_reads_arms(tmp_path):
    cfg = load_home_config(_write(tmp_path, "h.yaml", home_with_arms()))
    assert set(cfg.arms) == {"left", "right"}
    assert cfg.arms["right"].serial_number == "EF56GH78"


def test_arm_may_specify_port_instead_of_serial(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ port: "COM3", calibration_id: "left" }',
    )
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", text))
    assert cfg.arms["left"].port == "COM3"
    assert cfg.arms["left"].serial_number is None


def test_arm_with_both_port_and_serial_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ serial_number: "AB12CD34", port: "COM3", calibration_id: "left" }',
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_arm_with_neither_port_nor_serial_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ calibration_id: "left" }',
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_missing_arm_side_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '  right: { serial_number: "EF56GH78", calibration_id: "right" }\n', ""
    )
    with pytest.raises(ConfigError, match="right"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_gripper_safety_values_are_read(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    assert cfg.safety.gripper_max_step == 4.0
    assert cfg.safety.gripper_limits == (0.0, 100.0)


def test_max_step_for_uses_gripper_value_at_gripper_indices(tmp_path):
    from common.joints import GRIPPER_INDICES

    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    for idx in GRIPPER_INDICES:
        assert cfg.safety.max_step_for(idx) == 4.0
    for idx in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10):
        assert cfg.safety.max_step_for(idx) == cfg.safety.max_step_deg


def test_gripper_joint_limits_outside_percent_range_are_rejected(tmp_path):
    text = workbench_with_arms().replace(
        f"{LIMIT_INDENT}left_gripper: [0.0, 100.0]", f"{LIMIT_INDENT}left_gripper: [-30.0, 100.0]"
    )
    with pytest.raises(ConfigError, match="percent"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_shipped_config_files_still_load_after_stage2():
    w = load_workbench_config("config/workbench.yaml")
    h = load_home_config("config/home.yaml")
    assert set(w.arms) == {"left", "right"}
    assert set(h.arms) == {"left", "right"}
    assert w.safety.gripper_max_step > 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_config.py -v
```

기대: 새로 추가한 10개가 FAIL (`TypeError: SafetyConfig.__init__() got an unexpected keyword argument` 또는 `AttributeError: 'WorkbenchConfig' object has no attribute 'arms'`). 1단계의 9개는 계속 PASS.

- [ ] **Step 3: `common/config.py`를 확장한다**

`import` 에 추가:

```python
from common.joints import ARM_SIDES, GRIPPER_INDICES
```

`SafetyConfig` 를 교체한다 (필드 2개 추가 + 메서드 1개):

```python
@dataclass(frozen=True)
class SafetyConfig:
    align_threshold_deg: float
    max_step_deg: float
    follow_error_deg: float
    follow_error_hold_ms: int
    watchdog_ms: int
    joint_limits: dict[str, tuple[float, float]]
    #: 그리퍼는 단위가 퍼센트(0~100)라 도 단위 값과 섞을 수 없다 (스펙 §4.3).
    gripper_max_step: float = 4.0
    gripper_limits: tuple[float, float] = (0.0, 100.0)

    def max_step_for(self, index: int) -> float:
        """이 관절의 프레임당 최대 이동량. 그리퍼만 다른 값을 쓴다."""
        return self.gripper_max_step if index in GRIPPER_INDICES else self.max_step_deg
```

`ArmConfig` 를 추가한다:

```python
@dataclass(frozen=True)
class ArmConfig:
    """팔 한 대를 어떻게 찾고 어떤 캘리브레이션을 쓸지.

    serial_number 와 port 중 **정확히 하나**만 지정한다. 둘 다 주면 어느 쪽이
    이겼는지 모르는 상태가 되고, 좌우가 뒤바뀐 채 조종을 시작할 수 있다.
    """

    side: str
    serial_number: str | None
    port: str | None
    calibration_id: str
```

`arms` 파서를 추가한다:

```python
def _parse_arms(raw: Any) -> dict[str, ArmConfig]:
    if not isinstance(raw, dict):
        raise ConfigError("arms must be a mapping of side to arm settings")

    unknown = set(raw) - set(ARM_SIDES)
    if unknown:
        raise ConfigError(f"unknown arm side(s): {sorted(unknown)}, expected {list(ARM_SIDES)}")
    missing = set(ARM_SIDES) - set(raw)
    if missing:
        raise ConfigError(f"arms is missing side(s): {sorted(missing)}")

    arms: dict[str, ArmConfig] = {}
    for side in ARM_SIDES:
        entry = raw[side]
        if not isinstance(entry, dict):
            raise ConfigError(f"arms.{side} must be a mapping, got {entry!r}")
        serial_number = entry.get("serial_number")
        port = entry.get("port")
        if (serial_number is None) == (port is None):
            raise ConfigError(
                f"arms.{side}: specify exactly one of 'serial_number' or 'port' "
                "(serial_number is preferred; COM numbers move around)"
            )
        arms[side] = ArmConfig(
            side=side,
            serial_number=str(serial_number) if serial_number is not None else None,
            port=str(port) if port is not None else None,
            calibration_id=str(_require(entry, "calibration_id", f"arms.{side}")),
        )
    return arms
```

`_parse_joint_limits` 의 마지막 검증에 그리퍼 범위 확인을 추가한다. 기존 루프 안의 `limits[name] = (lo, hi)` 바로 앞에 넣는다:

```python
        if name.endswith("gripper") and not (0.0 <= lo < hi <= 100.0):
            raise ConfigError(
                f"joint_limits[{name}]: gripper units are percent, so limits must lie "
                f"within [0, 100], got [{lo}, {hi}]"
            )
```

`_parse_safety` 에 새 필드 2개를 넣는다:

```python
def _parse_safety(raw: Any) -> SafetyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("safety section must be a mapping")
    gripper_limits = raw.get("gripper_limits", [0.0, 100.0])
    if not (isinstance(gripper_limits, (list, tuple)) and len(gripper_limits) == 2):
        raise ConfigError(f"safety.gripper_limits must be [min, max], got {gripper_limits!r}")
    return SafetyConfig(
        align_threshold_deg=float(_require(raw, "align_threshold_deg", "safety")),
        max_step_deg=float(_require(raw, "max_step_deg", "safety")),
        follow_error_deg=float(_require(raw, "follow_error_deg", "safety")),
        follow_error_hold_ms=int(_require(raw, "follow_error_hold_ms", "safety")),
        watchdog_ms=int(_require(raw, "watchdog_ms", "safety")),
        joint_limits=_parse_joint_limits(_require(raw, "joint_limits", "safety")),
        gripper_max_step=float(raw.get("gripper_max_step", 4.0)),
        gripper_limits=(float(gripper_limits[0]), float(gripper_limits[1])),
    )
```

`WorkbenchConfig` 와 `HomeConfig` 에 `arms` 필드를 추가한다:

```python
@dataclass(frozen=True)
class WorkbenchConfig:
    use_mock: bool
    control_port: int
    video_port: int
    cameras: list[CameraConfig]
    safety: SafetyConfig
    arms: dict[str, ArmConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class HomeConfig:
    server_host: str
    control_port: int
    video_port: int
    use_mock: bool
    client_watchdog_ms: int
    arms: dict[str, ArmConfig] = field(default_factory=dict)
```

`field` 를 import 에 추가한다: `from dataclasses import dataclass, field`.

두 로더에 `arms` 를 넣는다. `arms` 섹션이 없으면 빈 dict 로 둔다 — 1단계의 mock 전용 설정이 계속 로드되어야 하기 때문이다:

```python
def load_workbench_config(path: str | Path) -> WorkbenchConfig:
    data = _read_yaml(path)
    return WorkbenchConfig(
        use_mock=bool(_require(data, "use_mock", "workbench config")),
        control_port=_check_port(_require(data, "control_port", "workbench config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "workbench config"), "video_port"),
        cameras=_parse_cameras(_require(data, "cameras", "workbench config")),
        safety=_parse_safety(_require(data, "safety", "workbench config")),
        arms=_parse_arms(data["arms"]) if "arms" in data else {},
    )


def load_home_config(path: str | Path) -> HomeConfig:
    data = _read_yaml(path)
    return HomeConfig(
        server_host=str(_require(data, "server_host", "home config")),
        control_port=_check_port(_require(data, "control_port", "home config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "home config"), "video_port"),
        use_mock=bool(_require(data, "use_mock", "home config")),
        client_watchdog_ms=int(_require(data, "client_watchdog_ms", "home config")),
        arms=_parse_arms(data["arms"]) if "arms" in data else {},
    )
```

- [ ] **Step 4: 실제 설정 파일에 `arms` 와 그리퍼 값을 넣는다**

`config/workbench.yaml` 의 `safety:` 블록에서 두 그리퍼 한계를 퍼센트로 바꾸고, 그리퍼 안전값과 `arms` 섹션을 추가한다. **시리얼 번호는 Task 2 Step 5에서 적어둔 실제 값으로 바꾼다.**

```yaml
safety:
  align_threshold_deg: 3.0
  max_step_deg: 1.5             # 몸통 관절. 60Hz 기준 90도/초
  gripper_max_step: 4.0         # 그리퍼는 퍼센트. 60Hz 기준 240%/초
  gripper_limits: [0.0, 100.0]
  follow_error_deg: 15.0
  follow_error_hold_ms: 500
  watchdog_ms: 200
  joint_limits:
    left_shoulder_pan:   [-120.0, 120.0]
    left_shoulder_lift:  [-120.0, 120.0]
    left_elbow_flex:     [-120.0, 120.0]
    left_wrist_flex:     [-120.0, 120.0]
    left_wrist_roll:     [-120.0, 120.0]
    left_gripper:        [0.0, 100.0]      # 퍼센트
    right_shoulder_pan:  [-120.0, 120.0]
    right_shoulder_lift: [-120.0, 120.0]
    right_elbow_flex:    [-120.0, 120.0]
    right_wrist_flex:    [-120.0, 120.0]
    right_wrist_roll:    [-120.0, 120.0]
    right_gripper:       [0.0, 100.0]      # 퍼센트

arms:
  left:  { serial_number: "CHANGEME_FOLLOWER_LEFT",  calibration_id: "follower_left" }
  right: { serial_number: "CHANGEME_FOLLOWER_RIGHT", calibration_id: "follower_right" }
```

`config/home.yaml` 에 추가한다:

```yaml
arms:
  left:  { serial_number: "CHANGEME_LEADER_LEFT",  calibration_id: "leader_left" }
  right: { serial_number: "CHANGEME_LEADER_RIGHT", calibration_id: "leader_right" }
```

`use_mock` 은 아직 두 파일 모두 `true` 로 남겨둔다. Task 7에서 바꾼다.

- [ ] **Step 5: 전체 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ -v
```

기대: 1단계 114 + Task 1의 13 + Task 2의 13 + Task 3의 10 = 150개 전부 PASS. **1단계 테스트가 하나도 깨지지 않아야 한다.**

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add arms config section and gripper-specific safety values"
```

---

### Task 4: 안전 게이트에 관절별 속도 제한 적용

`safety.py` 는 모든 관절에 `max_step_deg` 를 똑같이 쓴다. 그리퍼는 단위가 퍼센트라 이대로 두면 개폐가 4배 느려진다. `max_step_for(index)` 로 바꾼다.

**Files:**
- Modify: `workbench/safety.py` (`_follow` 의 속도 클램프 한 줄)
- Test: `tests/test_safety_clamps.py` (추가)

**Interfaces:**
- Consumes: `common.config.SafetyConfig.max_step_for(index)` (Task 3)
- Produces: 인터페이스 변경 없음. 동작만 관절별로 달라진다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_safety_clamps.py` 끝에 추가한다:

```python


# --- 2단계: 그리퍼는 단위가 퍼센트라 속도 제한이 다르다 --------------------


def test_gripper_uses_its_own_max_step():
    import dataclasses

    from common.joints import GRIPPER_INDICES

    base = make_config(max_step_deg=1.5)
    cfg = dataclasses.replace(
        base,
        gripper_max_step=4.0,
        gripper_limits=(0.0, 100.0),
        joint_limits={**base.joint_limits, "left_gripper": (0.0, 100.0), "right_gripper": (0.0, 100.0)},
    )
    gate = SafetyGate(cfg)
    t = engage(gate)

    target = [30.0] * N_JOINTS
    result = gate.step(packet(joints=target, clutch=True, seq=3), ZEROS, now=t + 0.02)

    for i in range(N_JOINTS):
        expected = 4.0 if i in GRIPPER_INDICES else 1.5
        assert result.targets[i] == pytest.approx(expected), f"joint {i}"


def test_body_joints_are_unaffected_by_gripper_max_step():
    """그리퍼 값을 크게 잡아도 몸통 관절은 도 단위 제한을 지켜야 한다."""
    import dataclasses

    cfg = dataclasses.replace(make_config(max_step_deg=1.5), gripper_max_step=50.0)
    gate = SafetyGate(cfg)
    t = engage(gate)
    result = gate.step(packet(joints=[30.0] * N_JOINTS, clutch=True, seq=3), ZEROS, now=t + 0.02)
    assert result.targets[0] == pytest.approx(1.5)
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_safety_clamps.py::test_gripper_uses_its_own_max_step -v
```

기대: FAIL — 그리퍼 자리도 1.5 가 나온다 (`assert 1.5 == 4.0 ± ...`, `joint 5`).

- [ ] **Step 3: `workbench/safety.py` 의 속도 클램프를 관절별로 바꾼다**

`_follow` 안의 클램프 부분에서 `cfg.max_step_deg` 를 `cfg.max_step_for(i)` 로 바꾼다:

```python
            # 2. 속도 제한 (그리퍼는 단위가 퍼센트라 별도 값을 쓴다, 스펙 §5.4)
            max_step = cfg.max_step_for(i)
            delta = limited - self._applied[i]
            step = min(max(delta, -max_step), max_step)
            if step != delta:
                flags |= Flag.SPEED_CLAMPED
```

- [ ] **Step 4: 전체 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ -v
```

기대: 152개 전부 PASS. 1단계의 클램프 테스트들은 `gripper_max_step` 기본값이 4.0 이지만 `make_config()` 의 `joint_limits` 가 그리퍼도 ±120 이므로 계속 통과한다. 하나라도 깨지면 멈추고 원인을 확인한다.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: apply per-joint speed clamp so grippers use percent units"
```

---

### Task 5: 실물 팔 어댑터 (`follower_arms.py`, `leader_arms.py`)

lerobot 의 `SOFollower`/`SOLeader` 2대씩을 1단계의 Protocol 로 감싼다. **실물이 있어야 검증되는 첫 코드**이므로, 하드웨어 없이 확인할 수 있는 부분(생성자 검증, 변환 연결)만 테스트하고 나머지는 Task 6의 진단 스크립트로 확인한다.

**Files:**
- Create: `workbench/follower_arms.py`, `home/leader_arms.py`
- Test: `tests/test_real_arms.py`

**Interfaces:**
- Consumes: `common.joints.to_arrays`/`to_dicts`/`ARM_SIDES`/`require_both_sides` (Task 1), `common.serial_ports.resolve_port_spec` (Task 2), `common.config.ArmConfig` (Task 3), `common.devices.FollowerArms`/`LeaderArms`
- Produces:
  - `workbench.follower_arms.RealFollowerArms(arms: dict[str, ArmConfig])` — `FollowerArms` Protocol 구현 + `connect()`, 속성 `is_connected: bool`
  - `home.leader_arms.RealLeaderArms(arms: dict[str, ArmConfig])` — `LeaderArms` Protocol 구현 + `connect()`, 속성 `is_connected: bool`

포트 조회와 좌우 검증은 **두 어댑터가 공용 함수를 호출한다.** 같은 코드를 두 파일에 복사하면 한쪽만 고쳐지는 사고가 난다.

`connect()` 를 `__init__` 과 분리하는 이유: 생성은 실패할 수 없어야 테스트가 쉽고, 하드웨어 접촉 시점을 호출자가 통제할 수 있다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_real_arms.py`:

```python
import pytest

from common.config import ArmConfig
from common.devices import FollowerArms, LeaderArms
from common.serial_ports import PortInfo
from home.leader_arms import RealLeaderArms
from workbench.follower_arms import RealFollowerArms

PORTS = [
    PortInfo(device="COM7", serial_number="FOLLOWER_L", description="USB Serial"),
    PortInfo(device="COM8", serial_number="FOLLOWER_R", description="USB Serial"),
]


def arm(side, serial_number=None, port=None, calibration_id="cal"):
    return ArmConfig(side=side, serial_number=serial_number, port=port, calibration_id=calibration_id)


def two_arms(**kwargs):
    return {
        "left": arm("left", serial_number="FOLLOWER_L", **kwargs),
        "right": arm("right", serial_number="FOLLOWER_R", **kwargs),
    }


def test_follower_requires_both_sides():
    with pytest.raises(ValueError, match="right"):
        RealFollowerArms(arms={"left": arm("left", serial_number="FOLLOWER_L")})


def test_leader_requires_both_sides():
    with pytest.raises(ValueError, match="left"):
        RealLeaderArms(arms={"right": arm("right", serial_number="FOLLOWER_R")})


def test_constructing_does_not_touch_hardware():
    """생성만으로는 시리얼 포트를 열지 않아야 한다. connect() 가 그 일을 한다."""
    follower = RealFollowerArms(arms=two_arms())
    leader = RealLeaderArms(arms=two_arms())
    assert follower.is_connected is False
    assert leader.is_connected is False


def test_adapters_satisfy_the_device_protocols():
    """1단계의 Protocol 을 실제로 만족해야 서버·클라이언트가 갈아끼울 수 있다."""
    assert isinstance(RealFollowerArms(arms=two_arms()), FollowerArms)
    assert isinstance(RealLeaderArms(arms=two_arms()), LeaderArms)


def test_reading_before_connect_is_an_error():
    with pytest.raises(RuntimeError, match="connect"):
        RealFollowerArms(arms=two_arms()).read_positions()
    with pytest.raises(RuntimeError, match="connect"):
        RealLeaderArms(arms=two_arms()).read_positions()


def test_writing_before_connect_is_an_error():
    with pytest.raises(RuntimeError, match="connect"):
        RealFollowerArms(arms=two_arms()).write_positions([0.0] * 12)


def test_close_before_connect_is_harmless():
    RealFollowerArms(arms=two_arms()).close()
    RealLeaderArms(arms=two_arms()).close()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_real_arms.py -v
```

기대: `ModuleNotFoundError: No module named 'workbench.follower_arms'`.

- [ ] **Step 3: `workbench/follower_arms.py`를 구현한다**

```python
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
```

- [ ] **Step 4: `home/leader_arms.py`를 구현한다**

```python
"""실물 리더 암 2대. lerobot 의 SOLeader 를 LeaderArms Protocol 로 감싼다.

리더는 읽기 전용이다. 토크를 끈 채로 두어 사람이 손으로 움직일 수 있게 한다.
"""

from __future__ import annotations

import logging
from typing import Sequence

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
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_real_arms.py -v
```

기대: 7개 전부 PASS. `test_adapters_satisfy_the_device_protocols` 가 특히 중요하다 — 1단계의 Protocol 을 실제로 만족하는지 확인하는 테스트다.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add real SO-101 arm adapters wrapping lerobot"
```

---

### Task 6: 실물 카메라 어댑터 + 하드웨어 진단 스크립트

**Files:**
- Create: `workbench/usb_camera.py`, `tools/probe_hardware.py`, `tools/__init__.py`
- Test: `tests/test_usb_camera.py`

**Interfaces:**
- Consumes: `common.devices.Camera`, `common.config.CameraConfig`
- Produces:
  - `workbench.usb_camera.UsbCamera(cam_id: int, name: str, index: int, width: int, height: int, fps: int)` — `Camera` Protocol 구현 + `open()`, 속성 `is_open: bool`, `actual_size: tuple[int, int] | None`
  - `workbench.usb_camera.CameraOpenError(Exception)`
  - `tools/probe_hardware.py` — `python -m tools.probe_hardware --ports | --arms | --cameras`

카메라는 실물이 있어야 검증되므로, 테스트는 열기 실패 경로와 Protocol 만족만 확인한다. 실제 영상은 진단 스크립트로 본다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_usb_camera.py`:

```python
import pytest

from common.devices import Camera
from workbench.usb_camera import CameraOpenError, UsbCamera


def make(index=999):
    return UsbCamera(cam_id=0, name="probe", index=index, width=320, height=240, fps=15)


def test_satisfies_camera_protocol():
    assert isinstance(make(), Camera)


def test_constructing_does_not_open_the_device():
    assert make().is_open is False


def test_read_before_open_returns_none():
    """카메라 1대가 죽어도 나머지가 계속 돌아야 한다 (스펙 §9). 예외를 던지지 않는다."""
    assert make().read() is None


def test_opening_a_nonexistent_index_raises():
    # 인덱스 999 에 카메라가 있을 수는 없다
    with pytest.raises(CameraOpenError, match="999"):
        make(index=999).open()


def test_close_before_open_is_harmless():
    make().close()
    make().close()


def test_actual_size_is_none_before_open():
    assert make().actual_size is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_usb_camera.py -v
```

기대: `ModuleNotFoundError: No module named 'workbench.usb_camera'`.

- [ ] **Step 3: `workbench/usb_camera.py`를 구현한다**

```python
"""USB 웹캠을 Camera Protocol 로 감싼다.

read() 는 실패해도 예외를 던지지 않고 None 을 돌려준다. 카메라 1대가 죽었다고
전체 조종이 멈추면 안 되고, CameraPublisher 가 그 프레임만 건너뛰면 되기
때문이다 (스펙 §9).
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger(__name__)


class CameraOpenError(Exception):
    """카메라를 열 수 없다."""


class UsbCamera:
    def __init__(self, cam_id: int, name: str, index: int, width: int, height: int, fps: int) -> None:
        self._cam_id = cam_id
        self._name = name
        self._index = index
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._actual_size: tuple[int, int] | None = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def actual_size(self) -> tuple[int, int] | None:
        """장치가 실제로 준 해상도. 요청값과 다를 수 있다."""
        return self._actual_size

    def open(self) -> None:
        # Windows 에서는 DirectShow 백엔드가 기본(MSMF)보다 열기가 빠르고 안정적이다.
        cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise CameraOpenError(
                f"camera {self._cam_id} ({self._name}): cannot open device index {self._index}. "
                "Run 'python -m tools.probe_hardware --cameras' to see which indices exist."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        # 드라이버가 프레임을 쌓아두면 영상이 뒤처진다. 최신성을 지키는 쪽을
        # 택한다 (스펙 §5.6). 지원하지 않는 드라이버에서는 조용히 무시된다.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise CameraOpenError(
                f"camera {self._cam_id} ({self._name}): opened device index {self._index} "
                "but the first frame read failed"
            )

        self._cap = cap
        self._actual_size = (frame.shape[1], frame.shape[0])
        if self._actual_size != (self._width, self._height):
            log.warning(
                "camera %d (%s): asked for %dx%d, device gave %dx%d",
                self._cam_id,
                self._name,
                self._width,
                self._height,
                *self._actual_size,
            )

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            log.warning("camera %d (%s): frame read failed", self._cam_id, self._name)
            return None
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
```

- [ ] **Step 4: `tools/probe_hardware.py`를 구현한다**

`tools/__init__.py` 는 빈 파일로 만든다.

```python
"""하드웨어를 하나씩 따로 점검하는 진단 스크립트.

조종 소프트웨어 전체를 띄우기 전에 이걸로 팔과 카메라를 개별 확인한다.
'안 움직인다'의 원인 후보를 서버·네트워크·안전로직에서 하드웨어로 좁히는 것이
목적이다.

    python -m tools.probe_hardware --ports
    python -m tools.probe_hardware --arms --config config/workbench.yaml
    python -m tools.probe_hardware --cameras
"""

from __future__ import annotations

import argparse
import logging
import time

from common.joints import GRIPPER_INDICES
from common.protocol import JOINT_NAMES
from common.serial_ports import describe_ports

log = logging.getLogger(__name__)


def probe_ports() -> int:
    print("serial ports:")
    print(describe_ports())
    print()
    print("Write the serial numbers into config/workbench.yaml (followers) and")
    print("config/home.yaml (leaders). To find out which arm is which, unplug one")
    print("and run this again.")
    return 0


def probe_arms(config_path: str, kind: str) -> int:
    """팔 2대를 열고 관절각을 3초간 읽어 출력한다. 아무것도 움직이지 않는다."""
    if kind == "follower":
        from common.config import load_workbench_config
        from workbench.follower_arms import RealFollowerArms

        cfg = load_workbench_config(config_path)
        arms = RealFollowerArms(arms=cfg.arms)
    else:
        from common.config import load_home_config
        from home.leader_arms import RealLeaderArms

        cfg = load_home_config(config_path)
        arms = RealLeaderArms(arms=cfg.arms)

    print(f"opening {kind} arms from {config_path} ...")
    arms.connect()
    try:
        print("reading for 3 seconds - move the arms by hand and watch the numbers")
        print("(indices 5 and 11 are grippers, unit is percent 0-100, not degrees)")
        deadline = time.monotonic() + 3.0
        reads = 0
        started = time.monotonic()
        last_print = 0.0
        while time.monotonic() < deadline:
            pos = arms.read_positions()
            reads += 1
            now = time.monotonic()
            if now - last_print > 0.5:
                last_print = now
                cells = []
                for i, v in enumerate(pos):
                    unit = "%" if i in GRIPPER_INDICES else ""
                    cells.append(f"{v:7.1f}{unit}")
                print("  " + " ".join(cells))
        elapsed = time.monotonic() - started
        print(f"\nread rate: {reads / elapsed:.1f} Hz over {reads} reads")
        print("This is the ceiling for the control loop. The spec assumes 60 Hz;")
        print("if this is well below that, lower the control rate in stage 3.")
    finally:
        arms.close()
    return 0


def probe_cameras(max_index: int) -> int:
    """어느 인덱스에 카메라가 있는지, 실제 해상도와 프레임레이트가 얼마인지."""
    from workbench.usb_camera import CameraOpenError, UsbCamera

    found = []
    for index in range(max_index):
        cam = UsbCamera(cam_id=index, name=f"index{index}", index=index, width=320, height=240, fps=15)
        try:
            cam.open()
        except CameraOpenError:
            continue
        try:
            started = time.monotonic()
            frames = 0
            while time.monotonic() - started < 1.0:
                if cam.read() is not None:
                    frames += 1
            print(f"index {index}: {cam.actual_size[0]}x{cam.actual_size[1]}  {frames} fps")
            found.append(index)
        finally:
            cam.close()

    if not found:
        print(f"no cameras found on indices 0..{max_index - 1}")
        return 1
    print()
    print(f"found camera indices: {found}")
    print("Put these into the 'index' fields of config/workbench.yaml cameras.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="probe SO-101 teleoperation hardware")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ports", action="store_true", help="list serial ports and serial numbers")
    group.add_argument("--arms", action="store_true", help="open arms and read joint angles")
    group.add_argument("--cameras", action="store_true", help="scan camera indices")
    parser.add_argument("--config", default="config/workbench.yaml")
    parser.add_argument(
        "--kind", choices=["follower", "leader"], default="follower", help="which arms --arms opens"
    )
    parser.add_argument("--max-index", type=int, default=8, help="how many camera indices to scan")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")

    if args.ports:
        return probe_ports()
    if args.arms:
        return probe_arms(args.config, args.kind)
    return probe_cameras(args.max_index)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ 2>&1 | tail -3
```

기대: 167개 전부 PASS (`test_usb_camera.py` 6개 + `test_camera_pub.py` 에 추가한 카메라 실패 2개).

- [ ] **Step 6: 실제 카메라 인덱스를 찾는다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.probe_hardware --cameras
```

기대: 연결한 카메라 3대의 인덱스와 실제 해상도·fps 가 나온다. **이 인덱스를 `config/workbench.yaml` 의 `index` 필드에 넣는다.** 요청한 320×240이 안 나오면 그 값을 설정에 반영한다 (장치가 지원하는 해상도만 쓸 수 있다).

- [ ] **Step 7: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add USB camera adapter and hardware probe tool"
```

---

### Task 7: 실물 조립 (`build_server`, `client.main`)

설정의 `use_mock` 으로 mock 과 실물을 갈아끼운다. **1단계의 제어 루프는 건드리지 않는다.**

**Files:**
- Modify: `workbench/server.py` (`build_server` 만)
- Modify: `home/client.py` (`main` 의 장치 생성 부분만)
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: Task 5의 `RealFollowerArms`/`RealLeaderArms`, Task 6의 `UsbCamera`
- Produces:
  - `workbench.server.build_server(cfg)` — `use_mock=False` 일 때 실물을 조립 (기존 시그니처 유지)
  - `home.client.build_leader(cfg)` — 새 함수. mock/실물 리더를 만든다

`client.main()` 에서 리더 생성을 `build_leader()` 로 빼는 이유: `main()` 은 GUI 루프라 테스트가 안 되지만, 조립 분기는 테스트할 수 있다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_build.py`:

```python
import pytest

from common.config import ArmConfig, CameraConfig, HomeConfig, WorkbenchConfig
from home.client import build_leader
from mock.fake_arms import FakeFollowerArms, FakeLeaderArms
from tests.test_safety_states import make_config
from workbench.server import build_server


def arms():
    return {
        "left": ArmConfig(side="left", serial_number="L", port=None, calibration_id="l"),
        "right": ArmConfig(side="right", serial_number="R", port=None, calibration_id="r"),
    }


def cameras():
    return [
        CameraConfig(id=i, name=f"cam{i}", index=i, width=320, height=240, fps=15, jpeg_quality=80)
        for i in range(3)
    ]


def workbench(use_mock):
    return WorkbenchConfig(
        use_mock=use_mock,
        control_port=0,
        video_port=0,
        cameras=cameras(),
        safety=make_config(),
        arms=arms(),
    )


def home(use_mock):
    return HomeConfig(
        server_host="127.0.0.1",
        control_port=0,
        video_port=0,
        use_mock=use_mock,
        client_watchdog_ms=300,
        arms=arms(),
    )


def test_mock_server_builds_fake_devices():
    server, publishers = build_server(workbench(use_mock=True))
    try:
        assert isinstance(server._follower, FakeFollowerArms)
        assert len(publishers) == 3
    finally:
        server.stop()
        for p in publishers:
            p.stop()


def test_mock_client_builds_fake_leader():
    leader = build_leader(home(use_mock=True))
    try:
        assert isinstance(leader, FakeLeaderArms)
    finally:
        leader.close()


def test_real_server_builds_real_adapters_without_touching_hardware():
    """조립까지는 하드웨어를 만지지 않아야 한다. 연결은 서버 start() 에서 한다."""
    from workbench.follower_arms import RealFollowerArms

    server, publishers = build_server(workbench(use_mock=False))
    try:
        assert isinstance(server._follower, RealFollowerArms)
        assert server._follower.is_connected is False
        assert len(publishers) == 3
    finally:
        for p in publishers:
            p.stop()


def test_real_client_builds_real_leader_without_touching_hardware():
    from home.leader_arms import RealLeaderArms

    leader = build_leader(home(use_mock=False))
    assert isinstance(leader, RealLeaderArms)
    assert leader.is_connected is False


def test_real_server_requires_arms_config():
    cfg = WorkbenchConfig(
        use_mock=False,
        control_port=0,
        video_port=0,
        cameras=cameras(),
        safety=make_config(),
        arms={},
    )
    with pytest.raises(ValueError, match="arms"):
        build_server(cfg)


def test_real_client_requires_arms_config():
    cfg = HomeConfig(
        server_host="127.0.0.1",
        control_port=0,
        video_port=0,
        use_mock=False,
        client_watchdog_ms=300,
        arms={},
    )
    with pytest.raises(ValueError, match="arms"):
        build_leader(cfg)
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_build.py -v
```

기대: `ImportError: cannot import name 'build_leader' from 'home.client'` 또는 `NotImplementedError` (1단계의 `build_server` 가 실물 요청을 거부한다).

- [ ] **Step 3: `workbench/server.py` 의 `build_server` 를 교체한다**

```python
def build_server(cfg: WorkbenchConfig) -> tuple[TeleopServer, list[CameraPublisher]]:
    """설정에 따라 mock/실물을 조립한다.

    조립 시점에는 하드웨어를 만지지 않는다. 실제 시리얼 포트와 카메라를 여는
    것은 TeleopServer.start() 와 CameraPublisher.start() 다.
    """
    if cfg.use_mock:
        from mock.fake_arms import FakeFollowerArms
        from mock.fake_cameras import FakeCamera

        follower = FakeFollowerArms()
        cameras = [
            FakeCamera(cam_id=c.id, name=c.name, width=c.width, height=c.height) for c in cfg.cameras
        ]
    else:
        from workbench.follower_arms import RealFollowerArms
        from workbench.usb_camera import UsbCamera

        if not cfg.arms:
            raise ValueError(
                "use_mock is false but the config has no 'arms' section; "
                "add serial numbers for the follower arms"
            )
        follower = RealFollowerArms(arms=cfg.arms)
        cameras = [
            UsbCamera(
                cam_id=c.id, name=c.name, index=c.index, width=c.width, height=c.height, fps=c.fps
            )
            for c in cfg.cameras
        ]

    publishers = [
        CameraPublisher(camera=cam, cam_id=c.id, fps=c.fps, jpeg_quality=c.jpeg_quality)
        for cam, c in zip(cameras, cfg.cameras)
    ]
    video = VideoServer(port=cfg.video_port, publishers=publishers)
    return TeleopServer(cfg=cfg, follower=follower, video=video), publishers
```

`TeleopServer.start()` 에서 실물 팔을 연결하도록 한 줄 추가한다. `_bind_control_socket()` 호출 **뒤**, `self._video.start()` **앞**에 넣는다 — 포트를 못 잡았으면 하드웨어를 건드리지 않고 죽는 편이 낫다:

```python
    def start(self) -> None:
        sock = self._bind_control_socket()
        self._sock = sock
        self.control_port = sock.getsockname()[1]

        # 실물 어댑터는 여기서 시리얼 포트를 연다. mock 에는 connect() 가 없다.
        connect = getattr(self._follower, "connect", None)
        if callable(connect):
            connect()

        if self._video is not None:
            self._video.start()
```

`CameraPublisher.start()` 에서 실물 카메라를 열도록 `workbench/camera_pub.py` 의 `start()` 를 교체한다.

**카메라 1대가 열리지 않아도 서버 전체가 죽으면 안 된다** (스펙 §9: "해당 카메라만 비활성, 나머지는 정상 동작"). 열기 실패는 로그를 남기고 그 카메라만 포기한다. `latest()` 가 계속 `None` 을 돌려주므로 클라이언트 화면에는 그 칸만 "no signal" 로 뜬다:

```python
    def start(self) -> None:
        if self._thread is not None:
            return
        # 실물 카메라는 여기서 장치를 연다. mock 에는 open() 이 없다.
        opener = getattr(self._camera, "open", None)
        if callable(opener):
            try:
                opener()
            except Exception:
                # 카메라 1대 때문에 조종 전체를 못 하게 만들지 않는다.
                # 이 퍼블리셔는 스레드를 띄우지 않고, latest() 는 None 으로 남는다.
                log.exception("camera %d: open failed, this camera is disabled", self._cam_id)
                return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"cam{self._cam_id}", daemon=True)
        self._thread.start()
```

`tests/test_camera_pub.py` 에 이 동작을 못 박는 테스트를 추가한다:

```python


def test_a_camera_that_fails_to_open_is_disabled_not_fatal():
    """카메라 1대가 죽어도 나머지 조종은 계속되어야 한다 (스펙 §9)."""

    class UnopenableCamera:
        def open(self):
            raise OSError("device index 999 not present")

        def read(self):
            return None

        def close(self):
            pass

    pub = CameraPublisher(camera=UnopenableCamera(), cam_id=7, fps=15, jpeg_quality=80)
    pub.start()  # 예외가 새어나오면 안 된다
    try:
        assert pub.latest() is None
    finally:
        pub.stop()


def test_other_cameras_keep_streaming_when_one_fails_to_open():
    class UnopenableCamera:
        def open(self):
            raise OSError("nope")

        def read(self):
            return None

        def close(self):
            pass

    good = make_publisher(cam_id=0)
    bad = CameraPublisher(camera=UnopenableCamera(), cam_id=1, fps=15, jpeg_quality=80)
    good.start()
    bad.start()
    server = VideoServer(port=0, publishers=[good, bad])
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            hb = recv_exactly(sock, VIDEO_HEADER_SIZE)
            assert hb is not None
            h = VideoHeader.unpack(hb)
            assert h is not None
            assert h.cam_id == 0  # 살아있는 카메라만 나간다
            assert recv_exactly(sock, h.length) is not None
    finally:
        server.stop()
        good.stop()
        bad.stop()
```

- [ ] **Step 4: `home/client.py` 에 `build_leader` 를 추가한다**

`main()` 위에 넣는다:

```python
def build_leader(cfg: HomeConfig):
    """설정에 따라 mock/실물 리더를 만든다. 하드웨어는 아직 만지지 않는다."""
    if cfg.use_mock:
        from mock.fake_arms import FakeLeaderArms

        return FakeLeaderArms()

    from home.leader_arms import RealLeaderArms

    if not cfg.arms:
        raise ValueError(
            "use_mock is false but the config has no 'arms' section; "
            "add serial numbers for the leader arms"
        )
    return RealLeaderArms(arms=cfg.arms)
```

`main()` 안의 리더 생성과 mock 거부를 교체한다. 기존의 `if not cfg.use_mock: raise NotImplementedError(...)` 블록과 `leader = FakeLeaderArms()` 를 지우고:

```python
    from home.hud import Hud, HudStats
    from home.video_recv import VideoClient

    leader = build_leader(cfg)
    connect = getattr(leader, "connect", None)
    if callable(connect):
        connect()
```

그리고 `M` 키(mock 리더 움직임 토글) 처리는 실물에서는 의미가 없으므로 속성 유무로 막는다. `main()` 루프의 해당 부분을 교체한다:

```python
            if action.toggle_motion and hasattr(leader, "motion_enabled"):
                leader.motion_enabled = not leader.motion_enabled
                log.info("mock leader motion: %s", leader.motion_enabled)
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ 2>&1 | tail -3
```

기대: 173개 전부 PASS. **1단계 테스트가 하나도 깨지지 않아야 한다** — 특히 mock 통합 테스트 7개가 계속 통과하는지 확인한다.

- [ ] **Step 6: mock 으로 회귀 확인**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m workbench.server --config config/workbench.yaml
```

별 터미널에서 클라이언트를 띄우고 1단계와 똑같이 동작하는지 확인한다 (`use_mock` 이 아직 `true`). 조립 코드를 바꿨으니 실물로 넘어가기 전에 mock 경로가 멀쩡한지 먼저 본다.

- [ ] **Step 7: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: wire real arms and cameras behind the use_mock switch"
```

---

### Task 8: 캘리브레이션과 하드웨어 개별 검증

여기서부터 **실제로 팔이 움직인다.** 손을 전원 스위치에 올려놓고 진행한다.

**Files:**
- Create: `docs/hardware-setup.md`
- Modify: `config/workbench.yaml`, `config/home.yaml` (실측값 반영)

**Interfaces:**
- Consumes: Task 6의 `tools/probe_hardware.py`, lerobot CLI
- Produces: 팔 4대의 캘리브레이션 파일, 확정된 시리얼 번호·카메라 인덱스

- [ ] **Step 1: 팔 4대의 시리얼 번호를 알아낸다**

팔 4대를 모두 USB 로 연결하고:

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.probe_hardware --ports
```

**어느 시리얼 번호가 어느 팔인지 알아내는 방법:** 팔 하나를 뽑고 다시 실행해서 사라진 줄을 확인한다. 4번 반복하면 4대가 다 매핑된다. 결과를 종이에 적어둔다.

- [ ] **Step 2: 팔 4대를 캘리브레이션한다**

각 팔마다 한 번씩, 총 4번 실행한다. **대화형이므로 화면 지시를 따라야 한다.** `--robot.port` 는 Step 1에서 알아낸 COM 포트를 넣는다.

팔로워 2대:

```bash
cd "C:/Users/flash/lerobot" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m lerobot.scripts.lerobot_calibrate --robot.type=so101_follower --robot.port=COM7 --robot.id=follower_left
```

```bash
cd "C:/Users/flash/lerobot" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m lerobot.scripts.lerobot_calibrate --robot.type=so101_follower --robot.port=COM8 --robot.id=follower_right
```

리더 2대:

```bash
cd "C:/Users/flash/lerobot" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m lerobot.scripts.lerobot_calibrate --teleop.type=so101_leader --teleop.port=COM3 --teleop.id=leader_left
```

```bash
cd "C:/Users/flash/lerobot" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m lerobot.scripts.lerobot_calibrate --teleop.type=so101_leader --teleop.port=COM4 --teleop.id=leader_right
```

절차는 각 팔마다 동일하다:
1. 팔을 가동 범위의 **중간** 자세로 놓고 ENTER — 이것이 영점이 된다
2. `wrist_roll` 을 제외한 모든 관절을 **끝에서 끝까지** 천천히 움직인다
3. ENTER 로 종료

**여기가 스펙 §5.2의 전제를 만드는 곳이다.** 리더의 0도와 팔로워의 0도가 같은 물리적 자세를 가리켜야 정렬 절차가 의미를 가진다. 4대 모두 "중간 자세"를 **같은 기준**으로 잡아야 한다 — 예를 들어 팔을 수직으로 세운 자세를 중간으로 정했다면 4대 다 그렇게 한다.

- [ ] **Step 3: 캘리브레이션 파일이 생겼는지 확인한다**

```bash
"C:/Users/flash/miniconda3/envs/lerobot/python.exe" -c "
from lerobot.utils.constants import HF_LEROBOT_CALIBRATION
import pathlib
for p in sorted(pathlib.Path(HF_LEROBOT_CALIBRATION).rglob('*.json')):
    print(p)
"
```

기대: `follower_left.json`, `follower_right.json`, `leader_left.json`, `leader_right.json` 4개가 보인다. 경로에 `robots/so_follower/` 와 `teleoperators/so_leader/` 가 들어간다.

4개가 다 없으면 멈추고 Step 2를 다시 한다.

- [ ] **Step 4: 리더 2대를 읽어본다 (팔은 안 움직인다)**

`config/home.yaml` 의 `arms` 시리얼 번호를 Step 1의 실제 값으로 바꾸고 `calibration_id` 를 `leader_left`/`leader_right` 로 맞춘 뒤:

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.probe_hardware --arms --kind leader --config config/home.yaml
```

확인할 것:
1. 두 팔이 다 열린다
2. 손으로 움직이면 숫자가 따라 변한다
3. **좌우가 맞다** — 왼팔을 움직였을 때 앞쪽 6개(인덱스 0~5)가 변해야 한다. 뒤쪽이 변하면 시리얼 번호가 뒤바뀐 것이다
4. 그리퍼(인덱스 5, 11)가 0~100 범위로 나온다
5. **읽기 레이트가 출력된다** — 이 숫자를 적어둔다. 60Hz 보다 크게 낮으면 3단계에서 제어 레이트를 낮춰야 한다

- [ ] **Step 5: 팔로워 2대를 읽어본다 (팔은 안 움직인다)**

`config/workbench.yaml` 의 `arms` 를 팔로워 시리얼 번호와 `follower_left`/`follower_right` 로 맞춘 뒤:

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.probe_hardware --arms --kind follower --config config/workbench.yaml
```

Step 4와 같은 5가지를 확인한다. 이 스크립트는 `write_positions` 를 호출하지 않으므로 팔이 스스로 움직이지는 않는다.

- [ ] **Step 6: 관절 한계를 실측한다**

lerobot 의 도구로 각 팔로워의 실제 가동 범위를 잰다:

```bash
cd "C:/Users/flash/lerobot" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m lerobot.scripts.lerobot_find_joint_limits --robot.type=so101_follower --robot.port=COM7 --robot.id=follower_left
```

출력된 범위에서 **양쪽으로 5~10도 안쪽으로 좁힌 값**을 `config/workbench.yaml` 의 `joint_limits` 에 넣는다. 실제 작업대에 장비를 배치한 뒤, 팔이 장비나 자기 몸통을 치는 자세를 배제하도록 더 좁힌다. 오른팔도 같이 한다.

그리퍼 2개는 퍼센트이므로 `[0.0, 100.0]` 을 유지한다.

- [ ] **Step 7: `docs/hardware-setup.md` 를 쓴다**

이 태스크에서 알아낸 것을 문서로 남긴다. 나중에 3명의 원격 사용자에게 장비를 나눠줄 때 같은 절차를 반복해야 하므로 필요하다.

```markdown
# 하드웨어 설정 기록

작성일: (실행한 날짜)

## 팔 4대

| 역할 | 시리얼 번호 | COM (변동 가능) | calibration_id | 읽기 레이트 |
|---|---|---|---|---|
| 리더 왼쪽 | | | leader_left | Hz |
| 리더 오른쪽 | | | leader_right | Hz |
| 팔로워 왼쪽 | | | follower_left | Hz |
| 팔로워 오른쪽 | | | follower_right | Hz |

COM 번호는 재부팅하면 바뀔 수 있으므로 참고용이다. 설정에는 시리얼 번호를 쓴다.

## 캘리브레이션 기준 자세

4대 모두 같은 기준으로 "중간 자세"를 잡아야 한다. 이번에 사용한 기준:

(예: 팔을 수직으로 세우고 그리퍼는 반쯤 벌린 자세)

## 카메라 3대

| 이름 | 인덱스 | 실제 해상도 | 실측 fps |
|---|---|---|---|
| front | | | |
| wrist_left | | | |
| wrist_right | | | |

## 실측 관절 한계

`lerobot-find-joint-limits` 결과와 최종적으로 설정에 넣은 값을 적는다.

## 다시 할 때

1. `python -m tools.probe_hardware --ports` 로 시리얼 번호 확인
2. 팔마다 `lerobot-calibrate` 1회
3. `python -m tools.probe_hardware --arms --kind {leader,follower}` 로 좌우 확인
4. `python -m tools.probe_hardware --cameras` 로 인덱스 확인
```

- [ ] **Step 8: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "docs: record hardware setup with measured serials, limits and rates"
```

---

### Task 9: 실물로 첫 조종

**Files:**
- Modify: `config/workbench.yaml`, `config/home.yaml` (`use_mock: false`)

**Interfaces:**
- Consumes: Task 1~8 전부
- Produces: 2단계 통과 판정

**안전 준비 — 아래를 다 하고 나서 시작한다:**
1. 작업대에서 **깨질 것을 모두 치운다.** 첫 조종에는 장비를 두지 않는다
2. 팔로워 2대 주변에 사람 팔 하나 길이의 빈 공간을 만든다
3. **전원 스위치나 USB 를 뽑을 수 있는 위치에 손을 둔다**
4. 리더 2대는 팔로워와 **비슷한 자세**로 미리 놓는다 (정렬을 쉽게 하기 위해)

- [ ] **Step 1: `use_mock` 을 끈다**

`config/workbench.yaml` 과 `config/home.yaml` 의 `use_mock` 을 `false` 로 바꾼다.

- [ ] **Step 2: 서버를 띄운다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m workbench.server --config config/workbench.yaml
```

기대 로그:
```
INFO follower left: opening COM7 (calibration id follower_left)
INFO follower right: opening COM8 (calibration id follower_right)
INFO video server listening on port 5556
INFO control server listening on UDP 5555
```

팔로워는 아직 **토크가 꺼져 있다** (DISCONNECTED). 손으로 움직여진다.

- [ ] **Step 3: 클라이언트를 띄운다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m home.client --config config/home.yaml
```

이 순간 팔로워에 **토크가 들어온다** (ALIGNING). 팔이 현재 자세를 붙들며 살짝 굳는 느낌이 난다. 크게 움직이면 뭔가 잘못된 것이니 즉시 전원을 끊는다.

- [ ] **Step 4: 정렬 절차를 확인한다**

화면의 관절 막대 12개를 보면서 **리더를 손으로 움직여 팔로워 자세에 맞춘다.**

확인할 것:
1. 리더를 움직이면 해당 관절의 막대가 반응한다
2. 좌우가 맞다 (왼쪽 리더 → 앞쪽 6개 막대)
3. 3도 안에 들어오면 막대가 초록으로 바뀐다
4. **12개가 다 초록이 아니면 스페이스를 눌러도 ENGAGED 로 가지 않는다**

3번에서 특정 관절이 절대 초록이 안 되면 **그 관절의 캘리브레이션이 리더와 팔로워 사이에 어긋난 것**이다. 두 팔의 그 관절을 눈으로 같은 자세로 맞췄을 때 화면 숫자가 다르면 확정이다. Task 8 Step 2를 그 팔에 대해 다시 한다.

- [ ] **Step 5: 처음으로 팔을 따라오게 한다**

12개가 다 초록인 상태에서:

1. **스페이스를 누른다** → `ENGAGED` (초록)
2. **리더 한쪽의 어깨 관절만 아주 조금(5도쯤) 천천히 움직인다**
3. 팔로워의 같은 관절이 같은 방향으로 따라오는지 본다

**방향이 반대로 가면 즉시 스페이스를 놓는다.** 그건 캘리브레이션의 `drive_mode` 문제이므로 그 팔을 다시 캘리브레이션한다.

따라온다면 범위를 조금씩 넓힌다. 관절 하나씩 → 팔 하나 전체 → 양팔.

- [ ] **Step 6: 안전장치 4개를 실물로 확인한다**

**하나씩, 순서대로.** 각 항목 사이에 R키 3초로 HOLD 를 풀어야 한다.

1. **클러치** — ENGAGED 중에 스페이스를 놓는다 → 팔로워가 즉시 그 자리에 멈추고 `ALIGNING` 으로 간다. 리더를 계속 움직여도 팔로워는 안 따라온다
2. **속도 클램프** — 리더를 빠르게 휘두른다 → `SPEED` 경고등이 켜지고 팔로워는 천천히 따라온다. 팔로워가 리더만큼 빠르게 움직이면 클램프가 안 걸린 것이다
3. **추종 오차** — ENGAGED 중에 **팔로워를 손으로 붙잡아 못 움직이게 한다.** 리더를 그 반대로 움직인다 → 0.5초 안에 `FOLLOW` 가 켜지고 `HOLD` 로 간다. 팔로워가 계속 밀어붙이면 즉시 놓고 전원을 끊는다
4. **워치독** — 클라이언트 터미널에서 Ctrl-C → 200ms 안에 팔로워가 그 자리에 멈춘다. 클라이언트를 다시 띄워도 `HOLD` 에 머물고 **저절로 움직이지 않는다**

3번이 이 단계에서 가장 중요한 검증이다. 실험실 장비를 지키는 장치가 실물에서 실제로 동작하는지 확인하는 것이기 때문이다. **장비 없는 상태에서 손으로 먼저 확인하는 이유가 이것이다.**

- [ ] **Step 7: 제어 레이트를 확인한다**

ENGAGED 상태로 30초쯤 조종하면서 HUD 의 `send` 값을 본다.

- **60Hz 근처** → 그대로 간다
- **크게 낮음** → Task 8 Step 4/5에서 적어둔 읽기 레이트가 원인이다. `LeaderSender(rate_hz=...)` 를 실측값의 80% 정도로 낮추고, 워치독 200ms 가 그 레이트에서 몇 패킷에 해당하는지 다시 계산한다 (60Hz 에서 12패킷이었다)

- [ ] **Step 8: 실제 작업 1건을 해본다**

깨지지 않는 물건(빈 플라스틱 통 등)을 작업대에 놓고 **집어서 옮긴다.**

여기서 판단할 것:
- 카메라 3대로 깊이 감이 잡히는가
- 그리퍼 개폐 속도(`gripper_max_step`)가 적절한가
- 90도/초가 너무 느리거나 빠르지 않은가
- 손목캠이 실제로 도움이 되는가

값을 조정하면 그 이유를 `docs/hardware-setup.md` 에 적는다.

- [ ] **Step 9: 2단계 통과 판정 (스펙 §11)**

| 기준 | 확인 |
|---|---|
| 리더를 움직이면 팔로워가 추종 | Step 5 |
| 정렬 절차 동작 | Step 4 |
| 클러치 해제 시 즉시 정지 | Step 6-1 |
| 손으로 막았을 때 추종 오차로 HOLD | Step 6-3 |
| 60Hz 유지 | Step 7 |

전부 통과하면 커밋하고 3단계로 넘어간다. 하나라도 실패하면 **그것만 고치고 다시 확인한다** — 여러 개를 한꺼번에 손대면 무엇이 고쳐졌는지 알 수 없다.

- [ ] **Step 10: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: switch to real hardware and record stage 2 verification"
```

---

## 3단계로 넘길 항목

- 폴더를 작업대 PC 로 복사하고 `config/home.yaml` 의 `server_host` 를 작업대 LAN IP 로
- Windows 방화벽 인바운드 규칙 (UDP 5555, TCP 5556)
- 바인딩 주소 확인 (`0.0.0.0` 으로 이미 되어 있다)
- LAN 에서 RTT <20ms 측정
- 랜선 분리 시 200ms 내 HOLD, 재연결 후 자동 시작 안 함
- **1단계에서 미룬 24시간 소킹** — 실물 팔로 하는 것이 더 의미 있으므로 3단계에서 함께 한다

## 미리 알아둘 위험

**캘리브레이션 불일치가 가장 흔한 실패다.** 4대를 서로 다른 "중간 자세"로 캘리브레이션하면 정렬 절차가 영원히 초록이 되지 않는다. Task 8 Step 2에서 기준 자세를 문서에 적어두는 이유가 이것이다.

**두 번째로 흔한 것은 좌우 뒤바뀜이다.** Task 8 Step 4/5의 좌우 확인을 건너뛰지 않는다. 실물에서 좌우가 뒤바뀌면 조종자가 왼쪽을 움직였는데 오른쪽 팔이 장비를 친다.

**그리퍼 단위 혼동.** 인덱스 5와 11은 퍼센트다. 이 자리에 도 단위 값이 들어가면 그리퍼가 즉시 한쪽 끝으로 붙는다. `joint_limits` 를 `[0, 100]` 으로 두는 것이 마지막 방어선이다.
