# 3단계(PC 2대 분리 + 카메라) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지금까지 한 PC 에서 돌던 원격 텔레오퍼레이션을 **작업대 PC(팔로워 2대 + 카메라 3대)** 와 **개발 PC(리더 2대, 집 역할)** 로 나누고, 같은 LAN 에서 실제로 조종되게 만든다.

**Architecture:** 코드는 한 줄도 새로 짜지 않아도 되는 것이 목표다 — 1·2단계에서 이미 `0.0.0.0` 에 바인드하고 `server_host` 를 설정으로 받게 만들어 두었다. 이번 단계에서 실제로 추가되는 것은 **배포 경로(GitHub)**, **방화벽**, **캘리브레이션 파일 이동**, 그리고 **연결이 안 될 때 원인을 좁혀줄 진단 도구**다. 2단계-B(카메라)를 여기에 합친다 — 카메라가 작업대 PC 로 가면 USB 포트 부족이 저절로 해소되기 때문이다.

**Tech Stack:** git/GitHub, Windows 방화벽(`netsh advfirewall`), Python 3.12 + lerobot 0.5.2 (작업대 PC 에 새로 설치), opencv-python-headless, pygame

**설계 스펙:** [`docs/specs/2026-07-31-remote-teleoperation-design.md`](../specs/2026-07-31-remote-teleoperation-design.md)
**하드웨어 기록:** [`docs/hardware-setup.md`](../hardware-setup.md)

**범위:** 스펙 §11 의 **3단계** + 2단계-B(카메라). 인터넷·포트포워딩(4단계)은 다음 계획이다.

**전제:** 작업대 PC 는 Windows. 두 PC 가 같은 LAN 에 있다. USB 카메라 3대를 준비한다.

---

## Global Constraints

- 개발 PC 인터프리터: `C:/Users/flash/miniconda3/envs/lerobot/python.exe`. 작업대 PC 는 Task 2에서 같은 이름의 conda 환경을 만든다.
- 작업 디렉터리는 양쪽 모두 경로에 **공백이 없어야 편하다.** 작업대 PC 에는 `C:\teleop` 처럼 공백 없는 경로에 클론한다(개발 PC 의 기존 경로는 그대로 둔다).
- **`common/protocol.py` 는 양쪽이 한 글자도 달라선 안 된다.** 현재 `PROTOCOL_MAGIC = b"RT02"`. 어긋나면 서버가 패킷을 거부하고 조종자 화면은 DISCONNECTED 로 남는다.
- **캘리브레이션 파일은 git 에 없다.** `%USERPROFILE%\.cache\huggingface\lerobot\calibration\` 에 있으며 **팔을 옮기면 그 팔의 파일도 옮겨야 한다.**
- 포트: UDP 5555(제어+텔레메트리), TCP 5556(영상). 서버는 이미 `0.0.0.0` 에 바인드한다.
- 안전값은 2단계에서 확정한 것을 그대로 쓴다: 정렬 3.0도, 조종 1.5도/프레임(90도/초), 그리퍼 4.0%/프레임, 추종오차 15도/500ms, 서버 워치독 200ms, 클라이언트 워치독 300ms, 호밍 0.5/프레임(30도/초).
- 클러치는 `--clutch toggle` 이 실사용 기본이다(2단계 결정, 풋페달 없음).
- 커밋 메시지는 영문 Conventional Commits.
- **작업대 PC 에서 팔이 처음 움직이는 단계는 사람이 전원 스위치에 손을 올려놓고 실행한다.**

---

## 이번 단계에서 바뀌는 것

| | 2단계-A (지금) | 3단계 (목표) |
|---|---|---|
| PC | 1대 (팔 4대 전부) | **2대** |
| 개발 PC | 서버 + 클라이언트 | **클라이언트만** (리더 2대) |
| 작업대 PC | — | **서버** (팔로워 2대 + 카메라 3대) |
| `server_host` | `127.0.0.1` | 작업대 PC 의 LAN IP |
| 카메라 | 0대 | **3대** |
| 조종자의 눈 | 실제 팔을 직접 봄 | **화면** |
| 새로 생기는 실패 원인 | — | 방화벽, 잘못된 IP, 캘리브레이션 누락, 버전 어긋남 |

---

## 파일 구조

| 파일 | 상태 | 책임 |
|---|---|---|
| `tools/check_link.py` | **신규** | 두 PC 사이 UDP/TCP 도달성과 프로토콜 버전을 조종 전에 확인 |
| `tools/move_calibration.py` | **신규** | 캘리브레이션 파일을 내보내기/가져오기 (git 에 없는 것) |
| `config/workbench.yaml` | 수정 | 카메라 3대 되살리기, 실측 인덱스 |
| `config/home.yaml` | 수정 | `server_host` 를 작업대 LAN IP 로 |
| `docs/deployment.md` | **신규** | 작업대 PC 세팅 절차 (원격 사용자 3명에게도 그대로 쓴다) |
| `.gitignore` | 수정 | 저장소를 GitHub 에 올리기 전 점검 |
| `README.md` | 수정 | 2대 구성 실행법 |

**코드 변경이 거의 없는 것이 정상이다.** 1·2단계에서 경계를 제대로 그어두었기 때문이다. 이번에 새로 만드는 두 도구는 기능이 아니라 **진단**이다 — "안 된다"의 원인 후보를 좁히기 위한 것.

---

### Task 1: 링크 진단 도구 (`tools/check_link.py`)

두 PC 를 연결할 때 실패 원인은 방화벽, 잘못된 IP, 서버 미기동, 버전 어긋남 넷이다. 전체 시스템을 띄워 놓고 원인을 찾으면 팔이 통전된 채로 디버깅하게 된다. **팔을 건드리기 전에** 링크만 따로 확인한다.

**Files:**
- Create: `tools/check_link.py`
- Test: `tests/test_check_link.py`

**Interfaces:**
- Consumes: `common.protocol` (`ControlPacket`, `TelemetryPacket`, `Cmd`, `CONTROL_SIZE`, `TELEMETRY_SIZE`, `PROTOCOL_MAGIC`, `VIDEO_HEADER_SIZE`, `VideoHeader`), `common.netutil.recv_exactly`, `common.config.load_home_config`
- Produces:
  - `LinkResult(ok: bool, detail: str)` (frozen dataclass)
  - `check_control(host: str, port: int, timeout: float = 3.0) -> LinkResult`
  - `check_video(host: str, port: int, timeout: float = 5.0) -> LinkResult`
  - `main(argv: list[str] | None = None) -> int`

`check_control` 은 진짜 제어 패킷 한 장을 보내고 텔레메트리가 돌아오는지 본다. `cmd=Cmd.NONE`, `clutch=False`, 관절값은 전부 0 이므로 **팔을 움직이지 않는다** — 서버는 DISCONNECTED→ALIGNING 으로 가며 현재 자세를 유지할 뿐이다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_check_link.py`:

```python
import pytest

from common.config import WorkbenchConfig
from mock.fake_arms import FakeFollowerArms
from mock.fake_cameras import FakeCamera
from tests.test_safety_states import make_config
from tools.check_link import LinkResult, check_control, check_video
from workbench.camera_pub import CameraPublisher, VideoServer
from workbench.server import TeleopServer


@pytest.fixture
def server():
    cfg = WorkbenchConfig(
        use_mock=True, control_port=0, video_port=0, cameras=[], safety=make_config()
    )
    srv = TeleopServer(cfg=cfg, follower=FakeFollowerArms(), video=None)
    srv.start()
    yield srv
    srv.stop()


def test_control_check_succeeds_against_a_live_server(server):
    result = check_control("127.0.0.1", server.control_port, timeout=3.0)
    assert result.ok is True
    assert "ALIGNING" in result.detail


def test_control_check_reports_no_reply_when_nothing_is_listening():
    """Windows 는 닫힌 UDP 포트에 대해 타임아웃이 아니라 ConnectionResetError 를
    던진다. 어느 경로로 오든 'no reply' 로 보고해야 사용자가 헷갈리지 않는다."""
    # 1024 미만 포트에는 우리 서버가 있을 수 없다
    result = check_control("127.0.0.1", 1, timeout=0.5)
    assert result.ok is False
    assert "no reply" in result.detail.lower()


def test_control_check_does_not_move_the_arm(server):
    """진단은 팔을 움직여서는 안 된다. clutch=0 이므로 ALIGNING 에 머문다."""
    from common.protocol import State

    check_control("127.0.0.1", server.control_port, timeout=3.0)
    assert server.state is State.ALIGNING


def test_video_check_succeeds_against_a_live_video_server():
    pub = CameraPublisher(
        camera=FakeCamera(cam_id=0, name="c", width=64, height=48),
        cam_id=0,
        fps=15,
        jpeg_quality=70,
    )
    pub.start()
    vs = VideoServer(port=0, publishers=[pub])
    vs.start()
    try:
        result = check_video("127.0.0.1", vs.port, timeout=5.0)
        assert result.ok is True
        assert "cam" in result.detail.lower()
    finally:
        vs.stop()
        pub.stop()


def test_video_check_reports_refused_when_nothing_is_listening():
    result = check_video("127.0.0.1", 1, timeout=0.5)
    assert result.ok is False
    assert result.detail


def test_link_result_is_falsy_friendly():
    assert LinkResult(ok=True, detail="x").ok is True
    assert LinkResult(ok=False, detail="y").ok is False
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_check_link.py -v
```

기대: `ModuleNotFoundError: No module named 'tools.check_link'`.

- [ ] **Step 3: `tools/check_link.py`를 구현한다**

```python
"""조종을 시작하기 전에 두 PC 사이의 링크만 따로 확인한다.

두 PC 로 나누면 새로운 실패 원인이 넷 생긴다: 방화벽, 잘못된 IP, 서버 미기동,
프로토콜 버전 어긋남. 전체 시스템을 띄워 놓고 찾으면 팔이 통전된 채로 디버깅하게
된다. 이 도구는 팔을 만지지 않고 링크만 본다.

제어 확인은 진짜 제어 패킷을 한 장 보낸다. clutch=0, cmd=NONE, 관절값 0 이므로
서버는 ALIGNING 으로 가며 현재 자세를 유지할 뿐 **움직이지 않는다**.

    python -m tools.check_link --host 192.168.0.42
    python -m tools.check_link --config config/home.yaml
"""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass

from common.netutil import recv_exactly
from common.protocol import (
    N_JOINTS,
    TELEMETRY_SIZE,
    VIDEO_HEADER_SIZE,
    Cmd,
    ControlPacket,
    TelemetryPacket,
    VideoHeader,
)


@dataclass(frozen=True)
class LinkResult:
    ok: bool
    detail: str


def check_control(host: str, port: int, timeout: float = 3.0) -> LinkResult:
    """제어 패킷 한 장을 보내고 텔레메트리가 돌아오는지 본다."""
    packet = ControlPacket(
        seq=1,
        t_send=time.time(),
        clutch=False,
        cmd=Cmd.NONE,
        joints=tuple([0.0] * N_JOINTS),
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        started = time.monotonic()
        sock.sendto(packet.pack(), (host, port))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            return LinkResult(
                False,
                f"no reply from {host}:{port}/udp within {timeout:.1f}s. "
                "Is the server running? Is the Windows firewall allowing UDP 5555?",
            )
        except ConnectionResetError:
            # Windows 는 닫힌 UDP 포트에 대해 ICMP port-unreachable 을 받으면 다음
            # recv 에서 WSAECONNRESET 을 던진다. 타임아웃이 아니라 '거절'이므로
            # 오히려 정보가 더 많다 - 방화벽이 아니라 서버가 안 떠 있는 것이다.
            return LinkResult(
                False,
                f"no reply from {host}:{port}/udp - the port is closed. "
                "The server is not running on that PC (a firewall block would time out instead).",
            )
        except OSError as exc:
            return LinkResult(False, f"send failed to {host}:{port}/udp: {exc}")
        rtt_ms = (time.monotonic() - started) * 1000.0

        if len(data) != TELEMETRY_SIZE:
            return LinkResult(
                False, f"reply was {len(data)} bytes, expected {TELEMETRY_SIZE}"
            )
        telemetry = TelemetryPacket.unpack(data)
        if telemetry is None:
            return LinkResult(
                False,
                "reply did not parse - the two sides are running different protocol "
                "versions. Pull the same commit on both PCs.",
            )
        return LinkResult(
            True, f"control ok, RTT {rtt_ms:.1f} ms, server state {telemetry.state.name}"
        )
    finally:
        sock.close()


def check_video(host: str, port: int, timeout: float = 5.0) -> LinkResult:
    """영상 TCP 에 붙어 프레임 한 장을 받아본다."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return LinkResult(
            False,
            f"cannot connect to {host}:{port}/tcp: {exc}. "
            "Is the server running? Is the Windows firewall allowing TCP 5556?",
        )
    try:
        sock.settimeout(timeout)
        header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
        if header_bytes is None:
            return LinkResult(False, "connected but the server closed without sending a frame")
        header = VideoHeader.unpack(header_bytes)
        if header is None:
            return LinkResult(
                False,
                "frame header did not parse - different protocol versions on the two PCs",
            )
        payload = recv_exactly(sock, header.length)
        if payload is None:
            return LinkResult(False, "frame header arrived but the image did not")
        return LinkResult(
            True, f"video ok, first frame from cam {header.cam_id}, {header.length} bytes"
        )
    except socket.timeout:
        return LinkResult(
            False,
            f"connected to {host}:{port}/tcp but no frame within {timeout:.1f}s. "
            "Are any cameras configured and working on the server?",
        )
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the link to the workbench PC")
    parser.add_argument("--host", help="workbench PC address; overrides the config")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--control-port", type=int)
    parser.add_argument("--video-port", type=int)
    parser.add_argument("--skip-video", action="store_true", help="only check the control link")
    args = parser.parse_args(argv)

    from common.config import load_home_config

    cfg = load_home_config(args.config)
    host = args.host or cfg.server_host
    control_port = args.control_port or cfg.control_port
    video_port = args.video_port or cfg.video_port

    print(f"checking {host} (control udp/{control_port}, video tcp/{video_port})")
    results = [("control", check_control(host, control_port))]
    if not args.skip_video:
        results.append(("video", check_video(host, video_port)))

    failed = False
    for name, result in results:
        mark = "OK  " if result.ok else "FAIL"
        print(f"  [{mark}] {name}: {result.detail}")
        failed = failed or not result.ok

    if failed:
        print()
        print("Nothing was moved. Fix the link before starting the client.")
        return 1
    print()
    print("Link is good. You can start the client.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_check_link.py -v
```

기대: 6개 전부 PASS. `test_control_check_does_not_move_the_arm` 이 특히 중요하다 — 진단이 하드웨어를 건드리지 않는다는 보장이다.

- [ ] **Step 5: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add link checker to diagnose the two-PC connection before teleoperating"
```

---

### Task 2: 캘리브레이션 파일 이동 도구 (`tools/move_calibration.py`)

**캘리브레이션 파일은 git 저장소에 없다.** `%USERPROFILE%\.cache\huggingface\lerobot\calibration\` 에 있다. 팔로워 2대가 작업대 PC 로 물리적으로 옮겨가므로 **그 팔의 캘리브레이션도 같이 가야 한다.** 안 가면 작업대 PC 가 캘리브레이션 없이 연결을 시도하고, 각도가 전혀 다른 값으로 읽혀 정렬이 영원히 안 맞는다.

**Files:**
- Create: `tools/move_calibration.py`
- Test: `tests/test_move_calibration.py`

**Interfaces:**
- Consumes: `common.joints.MOTOR_NAMES`
- Produces:
  - `class CalibrationError(Exception)`
  - `calibration_root() -> Path` — lerobot 의 캘리브레이션 폴더
  - `export_calibration(ids: list[str], dest: Path, root: Path | None = None) -> list[Path]` — 지정한 id 의 파일을 dest 로 복사
  - `import_calibration(src: Path, root: Path | None = None) -> list[Path]` — dest 폴더에서 캘리브레이션 폴더로 복사
  - `verify_calibration(ids: list[str], root: Path | None = None) -> list[str]` — 문제 목록. 빈 리스트면 정상
  - `main(argv: list[str] | None = None) -> int`

`root` 를 인자로 받는 이유: 테스트가 진짜 캘리브레이션 폴더를 건드리지 않게 하기 위함이다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_move_calibration.py`:

```python
import json

import pytest

from common.joints import MOTOR_NAMES
from tools.move_calibration import (
    CalibrationError,
    export_calibration,
    import_calibration,
    verify_calibration,
)


def good_calibration():
    return {
        name: {"id": i + 1, "drive_mode": 0, "homing_offset": 10, "range_min": 0, "range_max": 4095}
        for i, name in enumerate(MOTOR_NAMES)
    }


def make_root(tmp_path, ids_by_kind):
    """ids_by_kind: {"robots/so_follower": ["follower_left"], ...}"""
    root = tmp_path / "calibration"
    for subdir, ids in ids_by_kind.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        for cid in ids:
            (d / f"{cid}.json").write_text(json.dumps(good_calibration()), encoding="utf-8")
    return root


def test_export_copies_the_requested_ids(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left", "follower_right"]})
    dest = tmp_path / "out"
    copied = export_calibration(["follower_left", "follower_right"], dest, root=root)
    assert len(copied) == 2
    assert (dest / "robots" / "so_follower" / "follower_left.json").is_file()


def test_export_keeps_the_subdirectory_layout(tmp_path):
    """robots/ 와 teleoperators/ 가 섞이면 안 된다. lerobot 이 경로로 찾는다."""
    root = make_root(
        tmp_path,
        {"robots/so_follower": ["follower_left"], "teleoperators/so_leader": ["leader_left"]},
    )
    dest = tmp_path / "out"
    export_calibration(["follower_left", "leader_left"], dest, root=root)
    assert (dest / "robots" / "so_follower" / "follower_left.json").is_file()
    assert (dest / "teleoperators" / "so_leader" / "leader_left.json").is_file()


def test_export_rejects_an_unknown_id(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    with pytest.raises(CalibrationError, match="nope"):
        export_calibration(["nope"], tmp_path / "out", root=root)


def test_import_puts_files_back_in_the_same_layout(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    dest = tmp_path / "out"
    export_calibration(["follower_left"], dest, root=root)

    new_root = tmp_path / "other_pc"
    imported = import_calibration(dest, root=new_root)
    assert len(imported) == 1
    assert (new_root / "robots" / "so_follower" / "follower_left.json").is_file()


def test_import_refuses_to_silently_overwrite_a_different_file(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    dest = tmp_path / "out"
    export_calibration(["follower_left"], dest, root=root)

    other = make_root(tmp_path / "b", {"robots/so_follower": ["follower_left"]})
    target = other / "robots" / "so_follower" / "follower_left.json"
    target.write_text(json.dumps({"different": True}), encoding="utf-8")

    import_calibration(dest, root=other)
    backups = list((other / "robots" / "so_follower").glob("*.bak"))
    assert backups, "덮어쓰기 전에 백업을 남겨야 한다"


def test_verify_passes_for_complete_files(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    assert verify_calibration(["follower_left"], root=root) == []


def test_verify_reports_a_missing_id(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    problems = verify_calibration(["follower_left", "follower_right"], root=root)
    assert len(problems) == 1
    assert "follower_right" in problems[0]


def test_verify_reports_a_file_missing_motors(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    path = root / "robots" / "so_follower" / "follower_left.json"
    partial = good_calibration()
    del partial["gripper"]
    path.write_text(json.dumps(partial), encoding="utf-8")
    problems = verify_calibration(["follower_left"], root=root)
    assert len(problems) == 1
    assert "gripper" in problems[0]


def test_verify_reports_unparseable_json(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    (root / "robots" / "so_follower" / "follower_left.json").write_text("{ broken", encoding="utf-8")
    problems = verify_calibration(["follower_left"], root=root)
    assert len(problems) == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_move_calibration.py -v
```

기대: `ModuleNotFoundError: No module named 'tools.move_calibration'`.

- [ ] **Step 3: `tools/move_calibration.py`를 구현한다**

```python
"""캘리브레이션 파일을 PC 사이로 옮긴다.

**이 파일들은 git 저장소에 없다.** lerobot 이
``%USERPROFILE%\\.cache\\huggingface\\lerobot\\calibration\\`` 에 저장한다.
따라서 폴더를 아무리 동기화해도 따라가지 않는다.

팔로워 2대가 작업대 PC 로 물리적으로 옮겨가므로 그 팔의 캘리브레이션도 같이 가야
한다. 안 가면 작업대 PC 가 캘리브레이션 없이 연결을 시도하고, 관절각이 전혀 다른
값으로 읽혀 정렬이 영원히 맞지 않는다.

    # 개발 PC 에서 - 팔로워 2대 것만 내보낸다
    python -m tools.move_calibration --export follower_left follower_right --to D:\\cal

    # 작업대 PC 에서
    python -m tools.move_calibration --import-from D:\\cal
    python -m tools.move_calibration --verify follower_left follower_right
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from common.joints import MOTOR_NAMES


class CalibrationError(Exception):
    """캘리브레이션 파일을 찾거나 읽을 수 없다."""


def calibration_root() -> Path:
    from lerobot.utils.constants import HF_LEROBOT_CALIBRATION

    return Path(HF_LEROBOT_CALIBRATION)


def _find(cid: str, root: Path) -> Path:
    matches = [p for p in root.rglob(f"{cid}.json")]
    if not matches:
        raise CalibrationError(
            f"no calibration file for id {cid!r} under {root}. "
            "Run lerobot-calibrate for that arm first."
        )
    if len(matches) > 1:
        raise CalibrationError(f"id {cid!r} matches more than one file: {matches}")
    return matches[0]


def export_calibration(ids: list[str], dest: Path, root: Path | None = None) -> list[Path]:
    """지정한 id 의 파일을 dest 로 복사한다. 하위 폴더 구조를 그대로 유지한다.

    lerobot 은 robots/so_follower 와 teleoperators/so_leader 를 경로로 구분하므로
    평평하게 복사하면 반대편에서 못 찾는다.
    """
    root = calibration_root() if root is None else root
    dest = Path(dest)
    copied = []
    for cid in ids:
        src = _find(cid, root)
        target = dest / src.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(target)
    return copied


def import_calibration(src: Path, root: Path | None = None) -> list[Path]:
    """내보낸 폴더에서 이 PC 의 캘리브레이션 폴더로 복사한다.

    기존 파일이 있으면 .bak 으로 백업한 뒤 덮어쓴다. 조용히 덮어쓰면 어느 자세로
    캘리브레이션된 파일인지 알 수 없게 된다.
    """
    root = calibration_root() if root is None else root
    src = Path(src)
    if not src.is_dir():
        raise CalibrationError(f"not a directory: {src}")

    files = sorted(p for p in src.rglob("*.json"))
    if not files:
        raise CalibrationError(f"no calibration files under {src}")

    imported = []
    for path in files:
        target = root / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
        shutil.copy2(path, target)
        imported.append(target)
    return imported


def verify_calibration(ids: list[str], root: Path | None = None) -> list[str]:
    """각 id 의 파일이 있고 모터 6개가 다 들어 있는지 확인한다.

    Returns:
        문제 설명 목록. 빈 리스트면 정상.
    """
    root = calibration_root() if root is None else root
    problems = []
    for cid in ids:
        try:
            path = _find(cid, root)
        except CalibrationError as exc:
            problems.append(str(exc))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{cid}: cannot read {path}: {exc}")
            continue
        missing = set(MOTOR_NAMES) - set(data)
        if missing:
            problems.append(f"{cid}: {path} is missing motor(s) {sorted(missing)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="move lerobot calibration files between PCs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", nargs="+", metavar="ID", help="calibration ids to export")
    group.add_argument("--import-from", metavar="DIR", help="folder produced by --export")
    group.add_argument("--verify", nargs="+", metavar="ID", help="check ids exist and are complete")
    group.add_argument("--list", action="store_true", help="list every calibration file on this PC")
    parser.add_argument("--to", metavar="DIR", help="destination folder for --export")
    args = parser.parse_args(argv)

    root = calibration_root()

    if args.list:
        files = sorted(p for p in root.rglob("*.json"))
        if not files:
            print(f"no calibration files under {root}")
            return 1
        for p in files:
            print(f"  {p.relative_to(root)}")
        return 0

    if args.export:
        if not args.to:
            parser.error("--export needs --to DIR")
        copied = export_calibration(args.export, Path(args.to))
        for p in copied:
            print(f"  exported {p}")
        print()
        print("Copy that folder to the other PC, then run:")
        print(f"  python -m tools.move_calibration --import-from <folder>")
        return 0

    if args.import_from:
        imported = import_calibration(Path(args.import_from))
        for p in imported:
            print(f"  imported {p}")
        return 0

    problems = verify_calibration(args.verify)
    if problems:
        for p in problems:
            print(f"  PROBLEM {p}")
        return 1
    print(f"  all {len(args.verify)} calibration file(s) present and complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_move_calibration.py -v
```

기대: 9개 전부 PASS.

- [ ] **Step 5: 이 PC 의 캘리브레이션 파일을 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.move_calibration --verify leader_left leader_right follower_left follower_right
```

기대: `all 4 calibration file(s) present and complete`.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add tool to move lerobot calibration files between PCs"
```

---

### Task 3: GitHub 저장소로 올리기

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: 없음
- Produces: 두 PC 가 `git pull` 로 동기화할 수 있는 원격 저장소

- [ ] **Step 1: 저장소에 들어가면 안 되는 것이 없는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git ls-files | head -60 && echo "--- 총 파일 수 ---" && git ls-files | wc -l && echo "--- 큰 파일 ---" && git ls-files | xargs ls -la 2>/dev/null | sort -k5 -n -r | head -5
```

확인할 것: `.png`, `.log`, `.bak`, 캘리브레이션 JSON 이 없어야 한다. 시리얼 번호와 LAN IP 는 설정에 들어가지만 **비공개 저장소**이므로 괜찮다.

- [ ] **Step 2: `.gitignore`를 보강한다**

```
__pycache__/
*.pyc
.pytest_cache/
*.log
*.bak
*.png
```

`*.png` 를 넣는 이유: HUD 미리보기를 렌더링해 보는 일이 반복되는데 그것들이 저장소에 쌓이면 안 된다. 문서에 그림을 넣게 되면 그때 `docs/img/` 를 예외로 둔다.

- [ ] **Step 3: `README.md`를 2대 구성으로 갱신한다**

```markdown
# SO-101 Remote Teleoperation

집의 리더 암 2대로 작업대의 팔로워 암 2대를 카메라 영상을 보며 원격 조작한다.

- 설계: `docs/specs/2026-07-31-remote-teleoperation-design.md`
- 하드웨어 기록: `docs/hardware-setup.md`
- 작업대 PC 세팅: `docs/deployment.md`

## 구성

| | 작업대 PC | 집(개발) PC |
|---|---|---|
| 장치 | 팔로워 2대 + 카메라 3대 | 리더 2대 |
| 실행 | `workbench.server` | `home.client` |
| 포트 | UDP 5555 수신, TCP 5556 수신 | 없음 (먼저 접속) |

## 실행

작업대 PC:

    python -m workbench.server --config config/workbench.yaml

집 PC — 먼저 링크만 확인하고:

    python -m tools.check_link
    python -m home.client --config config/home.yaml --clutch toggle

## 테스트

    python -m pytest tests/ -v

## 진단 도구

    python -m tools.probe_hardware --ports          # 시리얼 번호
    python -m tools.probe_hardware --scan-motors    # 모터 1~6 응답 확인
    python -m tools.probe_hardware --check-sides --kind leader --config config/home.yaml
    python -m tools.probe_hardware --cameras        # 카메라 인덱스
    python -m tools.check_link                      # 두 PC 사이 링크
    python -m tools.move_calibration --list         # 캘리브레이션 파일
```

- [ ] **Step 4: GitHub 비공개 저장소를 만들고 올린다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && gh repo create so101-remote-teleoperation --private --source=. --remote=origin --push
```

`gh` 가 없거나 로그인이 안 되어 있으면 브라우저에서 비공개 저장소를 만든 뒤:

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git remote add origin https://github.com/<사용자>/so101-remote-teleoperation.git && git branch -M main && git push -u origin main
```

- [ ] **Step 5: 올라간 것을 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git remote -v && git log --oneline -1 && git status --short --branch | head -2
```

기대: `origin` 이 보이고, 로컬과 원격이 같은 커밋을 가리킨다.

- [ ] **Step 6: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "docs: update README for the two-PC layout and tighten gitignore" && git push
```

---

### Task 4: 작업대 PC 세팅

여기서부터 **작업대 PC 에서** 실행한다. 개발 PC 에서 하는 것과 헷갈리지 않게, 각 명령이 어느 PC 것인지 표시한다.

**Files:**
- Create: `docs/deployment.md`

**Interfaces:**
- Consumes: Task 2의 `tools/move_calibration.py`, Task 3의 GitHub 저장소
- Produces: 작업대 PC 에서 `python -m workbench.server` 가 뜨는 상태

- [ ] **Step 1: [작업대 PC] Miniconda 와 환경을 만든다**

Miniconda 가 없으면 https://docs.conda.io/en/latest/miniconda.html 에서 설치한다. 그 다음:

```bash
conda create -n lerobot python=3.12 -y
conda activate lerobot
```

- [ ] **Step 2: [작업대 PC] lerobot 과 의존성을 설치한다**

```bash
pip install "lerobot==0.5.2" pyserial pygame pytest opencv-python-headless PyYAML numpy
```

`pygame` 은 작업대 PC 에서 화면을 띄우지 않지만, `tests/` 가 import 하므로 넣는다.

설치 확인:

```bash
python -c "import lerobot, serial, cv2, yaml, numpy, pygame; print('lerobot', lerobot.__version__); print('cv2', cv2.__version__)"
```

기대: `lerobot 0.5.2` 와 cv2 버전이 나온다.

- [ ] **Step 3: [작업대 PC] 저장소를 클론한다**

**공백 없는 경로**에 클론한다. 개발 PC 의 경로에는 공백이 있어 명령마다 따옴표가 필요했는데, 새로 만드는 쪽은 그럴 이유가 없다.

```bash
git clone https://github.com/<사용자>/so101-remote-teleoperation.git C:\teleop
cd C:\teleop
python -m pytest tests/ -q
```

기대: 전체 테스트가 통과한다. **여기서 실패하면 하드웨어를 붙이기 전에 원인을 찾는다** — 환경 문제이지 배선 문제가 아니다.

- [ ] **Step 4: [작업대 PC] 방화벽 규칙을 넣는다**

**관리자 권한 PowerShell** 에서:

```powershell
New-NetFirewallRule -DisplayName "SO101 teleop control (UDP 5555)" -Direction Inbound -Protocol UDP -LocalPort 5555 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "SO101 teleop video (TCP 5556)" -Direction Inbound -Protocol TCP -LocalPort 5556 -Action Allow -Profile Private
```

`-Profile Private` 로 제한하는 이유: 공용 네트워크에서까지 포트를 열 이유가 없다. 작업대 PC 의 네트워크 프로필이 "공용"으로 잡혀 있으면 규칙이 적용되지 않으므로, 그때는 프로필을 개인으로 바꾸거나 `-Profile Any` 로 다시 만든다.

확인:

```powershell
Get-NetFirewallRule -DisplayName "SO101*" | Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize
```

- [ ] **Step 5: [작업대 PC] LAN IP 를 확인한다**

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize
```

**이 IP 를 적어둔다.** 집 PC 의 `config/home.yaml` 에 넣을 값이다.

> IP 가 DHCP 로 바뀔 수 있다. 공유기에서 이 PC 의 MAC 에 **고정 IP 할당**을 걸어두면 매번 바뀌지 않는다. 4단계 포트포워딩에서도 어차피 필요하다.

- [ ] **Step 6: [작업대 PC] 팔로워 2대를 연결하고 시리얼 번호를 확인한다**

팔로워 2대의 USB 를 작업대 PC 로 옮겨 꽂는다.

```bash
cd C:\teleop && python -m tools.probe_hardware --ports
```

기대: 시리얼 번호 `5B14033734`(왼쪽), `5B14031059`(오른쪽) 가 보인다. **COM 번호는 개발 PC 와 다를 수 있는데 상관없다** — 설정이 시리얼 번호를 쓰기 때문이다. 이것이 시리얼 번호로 지정한 이유다.

```bash
cd C:\teleop && python -m tools.probe_hardware --scan-motors
```

기대: 모터 1~6 이 전부 `O`.

- [ ] **Step 7: [개발 PC] 팔로워 캘리브레이션을 내보낸다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.move_calibration --export follower_left follower_right --to D:\cal
```

`D:\cal` 을 USB 나 공유폴더 경로로 바꾼다. **리더 2대 것은 내보내지 않는다** — 리더는 개발 PC 에 남는다.

- [ ] **Step 8: [작업대 PC] 캘리브레이션을 가져오고 확인한다**

```bash
cd C:\teleop && python -m tools.move_calibration --import-from D:\cal && python -m tools.move_calibration --verify follower_left follower_right
```

기대: `all 2 calibration file(s) present and complete`.

**이 단계를 건너뛰면** 서버는 캘리브레이션 없이 연결하고, 관절각이 전혀 다른 값으로 읽혀 정렬 막대가 영원히 초록이 되지 않는다.

- [ ] **Step 9: [작업대 PC] 서버를 띄워본다 (카메라 없이)**

`config/workbench.yaml` 은 아직 `cameras: []` 다.

```bash
cd C:\teleop && python -m workbench.server --config config/workbench.yaml
```

기대:
```
INFO  workbench.follower_arms: follower left: opening COM? (calibration id follower_left)
INFO  lerobot...: follower_left SOFollower connected.
INFO  workbench.follower_arms: follower right: opening COM? ...
INFO  workbench.camera_pub: video server listening on port 5556
INFO  __main__: control server listening on UDP 5555
```

팔로워는 늘어져 있어야 한다(DISCONNECTED, 토크 꺼짐). Ctrl-C 로 끈다.

- [ ] **Step 10: `docs/deployment.md`를 쓴다**

이 태스크에서 한 것을 절차로 남긴다. **원격 사용자 3명에게 리더 세트를 배포할 때 거의 같은 절차를 반복하므로** 필요하다.

```markdown
# 작업대 PC / 원격 사용자 PC 세팅 절차

작성일: (실행한 날짜)

## 공통 (어느 PC든)

1. Miniconda 설치
2. `conda create -n lerobot python=3.12 -y && conda activate lerobot`
3. `pip install "lerobot==0.5.2" pyserial pygame pytest opencv-python-headless PyYAML numpy`
4. `git clone <저장소> C:\teleop` — **공백 없는 경로에**
5. `cd C:\teleop && python -m pytest tests/ -q` — 하드웨어를 붙이기 전에 통과해야 한다

## 작업대 PC 추가 절차

6. 방화벽 (관리자 PowerShell):
   - UDP 5555 인바운드 허용
   - TCP 5556 인바운드 허용
7. LAN IP 확인 → 공유기에서 고정 IP 할당
8. 팔로워 2대 USB 연결 → `probe_hardware --ports`, `--scan-motors`
9. 캘리브레이션 가져오기 → `move_calibration --import-from`, `--verify`
10. 카메라 3대 연결 → `probe_hardware --cameras` → `config/workbench.yaml` 의 index 반영

## 원격 사용자 PC 추가 절차 (4단계에서)

6. 리더 2대 USB 연결 → `probe_hardware --ports` → `config/home.yaml` 시리얼 번호
7. 리더 2대 캘리브레이션 (`lerobot-calibrate`) — **작업대의 팔로워와 같은 기준 자세로**
8. `probe_hardware --check-sides --kind leader` 로 좌우 확인
9. `config/home.yaml` 의 `server_host` 를 작업대 주소로
10. `check_link` 로 링크 확인 후 클라이언트 실행

## 기록해둘 값

| 항목 | 값 |
|---|---|
| 작업대 PC LAN IP | |
| 작업대 PC 경로 | `C:\teleop` |
| 저장소 URL | |
| 카메라 인덱스 (front / wrist_left / wrist_right) | |
```

- [ ] **Step 11: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "docs: add deployment procedure for the workbench PC" && git push
```

---

### Task 5: LAN 으로 붙이기 (카메라 없이)

**한 번에 하나씩.** 카메라를 먼저 붙이면 "안 된다"의 원인이 링크인지 카메라인지 모른다.

**Files:**
- Modify: `config/home.yaml` (`server_host`)

**Interfaces:**
- Consumes: Task 1의 `tools/check_link.py`, Task 4의 작업대 PC
- Produces: 두 PC 사이에서 실제로 조종되는 상태

- [ ] **Step 1: [개발 PC] `server_host`를 작업대 IP 로 바꾼다**

`config/home.yaml`:

```yaml
server_host: "192.168.0.42"   # 작업대 PC LAN IP. Task 4 Step 5 에서 확인한 값으로.
```

- [ ] **Step 2: [작업대 PC] 서버를 띄운다**

```bash
cd C:\teleop && python -m workbench.server --config config/workbench.yaml
```

- [ ] **Step 3: [개발 PC] 팔을 건드리기 전에 링크만 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.check_link --skip-video
```

기대:
```
  [OK  ] control: control ok, RTT 1.2 ms, server state ALIGNING
```

**실패하면 여기서 멈추고 원인을 잡는다.** 메시지가 어디를 볼지 알려준다:

| 증상 | 원인 후보 |
|---|---|
| `no reply ... within 3.0s` | 서버 미기동 / 방화벽 / IP 오타 |
| `reply did not parse` | 두 PC 의 커밋이 다르다 → 양쪽에서 `git pull` |
| `send failed` | 네트워크 자체가 안 됨 → `ping <IP>` 부터 |

- [ ] **Step 4: [개발 PC] 클라이언트를 띄운다 (카메라 없이)**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m home.client --config config/home.yaml --cameras 0 --clutch toggle
```

**이 순간 작업대의 팔로워에 토크가 들어온다.** 작업대에 사람이 있어야 하고, 깨질 것을 치워둔다.

확인할 것:
1. 상태가 `ALIGNING`(주황)
2. `RTT` 가 **20ms 미만** (스펙 §11 3단계 통과 기준)
3. `send` 가 60 Hz 근처
4. 리더를 움직이면 관절 막대가 반응
5. 12개를 초록으로 맞춘 뒤 스페이스 → `ENGAGED`, 팔로워가 따라옴

- [ ] **Step 5: 랜선을 뽑아 워치독을 확인한다**

ENGAGED 상태에서 **개발 PC 의 랜선을 뽑거나 Wi-Fi 를 끈다.**

기대:
1. 200ms 안에 작업대의 팔로워가 그 자리에 정지
2. 클라이언트 화면에 **빨간 테두리 + `LINK LOST`**
3. 작업대 서버 로그에 `watchdog fired: no control packet for ??? ms`
4. **랜선을 다시 꽂아도 팔이 저절로 움직이지 않는다** (HOLD 유지)

4번이 무인 운영의 마지노선이다. 자동으로 재개되면 멈추고 원인을 잡는다.

- [ ] **Step 6: 호밍을 실물로 확인한다 (2단계에서 미룬 것)**

HOLD 상태에서:
1. **R 3초** → 화면이 `HOMING`(파랑) + `hold the clutch to walk the follower home`
2. **스페이스** → 팔로워가 30도/초로 천천히 home 자세로
3. **스페이스 놓으면** 그 자리 정지, 다시 누르면 이어서
4. 도착하면 자동으로 `ALIGNING`
5. **그리퍼는 움직이지 않는다**

호밍은 단위 테스트만 통과했고 실물에서 처음 도는 것이다. 예상 밖으로 움직이면 스페이스를 놓거나 ESC.

- [ ] **Step 7: 3단계 링크 통과 판정 (스펙 §11)**

| 기준 | 확인 |
|---|---|
| LAN RTT < 20ms | Step 4 |
| 랜선 분리 시 200ms 내 HOLD | Step 5 |
| 재연결 후 자동 시작 안 함 | Step 5 |
| 호밍 실물 동작 | Step 6 |

- [ ] **Step 8: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "config: point the client at the workbench PC over LAN" && git push
```

---

### Task 6: 카메라 3대 붙이기 (2단계-B)

**Files:**
- Modify: `config/workbench.yaml` (`cameras`)

**Interfaces:**
- Consumes: 1단계의 `workbench/usb_camera.py`, `workbench/camera_pub.py`, Task 1의 `check_video`
- Produces: 영상 3채널이 흐르는 상태

- [ ] **Step 1: [작업대 PC] 카메라 3대를 연결하고 인덱스를 찾는다**

```bash
cd C:\teleop && python -m tools.probe_hardware --cameras
```

기대: 인덱스 3개와 각각의 실제 해상도·fps 가 나온다. **요청한 320×240 이 안 나오면** 장치가 지원하는 값을 그대로 설정에 쓴다.

> USB 대역폭 주의: 카메라 3대를 같은 USB 허브에 물리면 대역폭이 부족해 프레임이 떨어질 수 있다. 가능하면 **서로 다른 USB 컨트롤러**에 나눠 꽂고, `--cameras` 출력의 fps 가 15 근처인지 확인한다.

- [ ] **Step 2: [작업대 PC] `config/workbench.yaml`의 카메라를 되살린다**

주석 처리해둔 블록을 실측 인덱스로 되살린다:

```yaml
cameras:
  - { id: 0, name: front,       index: 0, width: 320, height: 240, fps: 15, jpeg_quality: 80 }
  - { id: 1, name: wrist_left,  index: 1, width: 320, height: 240, fps: 15, jpeg_quality: 80 }
  - { id: 2, name: wrist_right, index: 2, width: 320, height: 240, fps: 15, jpeg_quality: 80 }
```

`index` 를 Step 1 의 실측값으로 바꾼다. **어느 인덱스가 어느 위치의 카메라인지**는 하나씩 가려보며 확인한다.

- [ ] **Step 3: [작업대 PC] 서버를 다시 띄운다**

```bash
cd C:\teleop && python -m workbench.server --config config/workbench.yaml
```

기대: 카메라 열기 실패 로그가 없어야 한다. 한 대만 실패하면 그 카메라만 비활성되고 서버는 계속 뜬다(스펙 §9) — 로그에 `camera N: open failed, this camera is disabled` 가 남는다.

- [ ] **Step 4: [개발 PC] 영상 링크를 먼저 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.check_link
```

기대: control 과 video 둘 다 `OK`.

- [ ] **Step 5: [개발 PC] 카메라 3대로 클라이언트를 띄운다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m home.client --config config/home.yaml --cameras 3 --clutch toggle
```

확인할 것:
1. 영상 3개가 다 보이고 멈추지 않는다
2. **손을 카메라 앞에서 흔들었을 때 화면 지연이 체감상 견딜 만한가**
3. `RTT` 는 여전히 20ms 미만인가 (영상이 제어를 밀어내지 않는지)
4. `send` 가 60 Hz 를 유지하는가

3·4번이 중요하다. 스펙 §3 이 제어와 영상을 다른 채널로 분리한 이유가 바로 이것이므로, **실제로 그렇게 되는지** 여기서 확인한다.

- [ ] **Step 6: 카메라 1대를 뽑아 격리를 확인한다 (스펙 §9)**

서버를 띄운 상태에서 카메라 하나의 USB 를 뽑는다.

기대: 그 칸만 `no signal` 이 되고 **나머지 2대와 조종은 계속된다.** 서버가 죽으면 격리가 안 된 것이다.

- [ ] **Step 7: 대역폭을 실측한다**

스펙 §3 은 320×240 15fps 3대에 4.3 Mbps 를 추정했다. **합성 영상 기준이므로 실제 센서 영상은 더 클 수 있다.** 작업대 PC 에서 확인한다:

```powershell
$before = (Get-NetAdapterStatistics | Measure-Object -Property SentBytes -Sum).Sum
Start-Sleep -Seconds 10
$after = (Get-NetAdapterStatistics | Measure-Object -Property SentBytes -Sum).Sum
"{0:N2} Mbps" -f ((($after - $before) * 8) / 10 / 1000000)
```

클라이언트가 붙어 있는 동안 실행한다. **이 값에는 작업대 PC 의 다른 네트워크 트래픽도 섞인다** — 정확한 측정이 아니라 자릿수 확인용이다. 더 정확히 보려면 클라이언트를 끈 상태로 한 번 재고 그 차이를 쓴다.

추정치(4.3 Mbps)의 2배를 넘으면 `jpeg_quality` 를 낮추거나 해상도를 재검토한다 — 4단계 인터넷에서는 작업대 **업로드** 대역폭을 쓰기 때문이다.

- [ ] **Step 8: 카메라만 보고 조종해본다**

**작업대의 팔이 안 보이는 위치로 이동**하거나 등을 돌리고, 화면만 보면서 물건 하나를 집어 옮긴다.

여기서 처음으로 판단할 수 있는 것들:
- 카메라 3대로 **깊이 감**이 잡히는가
- 손목캠이 실제로 도움이 되는가
- 정면 카메라의 위치·각도를 바꿔야 하는가
- 15fps 가 답답한가 (320×240 이면 30fps 로 올려도 대역폭 여유가 있다)

- [ ] **Step 9: `docs/hardware-setup.md`의 카메라 표를 채운다**

| 이름 | 인덱스 | 실제 해상도 | 실측 fps |
|---|---|---|---|
| front | | | |
| wrist_left | | | |
| wrist_right | | | |

실측 대역폭과 Step 8 에서 조정한 값(있다면)도 함께 적는다.

- [ ] **Step 10: 커밋**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "config: enable the three cameras and record measured indices" && git push
```

---

### Task 7: 24시간 소킹 (1단계에서 미룬 것)

실물 팔과 실제 링크로 돌리는 것이 mock 보다 훨씬 의미 있으므로 여기서 한다.

**Files:**
- Create: `tools/soak.py`

**Interfaces:**
- Consumes: `home.client` (`ControlLink`, `CommandState`, `LeaderSender`), `home.video_recv.VideoClient`, `home.leader_arms.RealLeaderArms`, `common.config.load_home_config`
- Produces: `tools/soak.py` — 화면 없이 오래 돌리며 메모리와 링크를 기록

**팔은 ENGAGED 로 두지 않는다.** 24시간 동안 팔이 움직이면 위험하고 의미도 없다. `ALIGNING` 으로 두고 **네트워크·영상·메모리**만 본다. 클램프와 추종오차 경로는 이미 단위 테스트와 2단계 실물 검증으로 확인했다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_soak.py`:

```python
import pytest

from tools.soak import SoakSample, format_samples, should_stop


def test_sample_holds_what_we_measure():
    s = SoakSample(t=1.0, rss_mb=120.5, rtt_ms=3.2, lost=0, video_ok=True, state="ALIGNING")
    assert s.rss_mb == 120.5
    assert s.video_ok is True


def test_format_samples_is_csv_with_a_header():
    rows = format_samples([SoakSample(1.0, 120.5, 3.2, 0, True, "ALIGNING")])
    lines = rows.splitlines()
    assert lines[0].startswith("t_s,rss_mb,rtt_ms,lost,video_ok,state")
    assert "120.5" in lines[1]
    assert "ALIGNING" in lines[1]


def test_format_samples_handles_a_missing_rtt():
    rows = format_samples([SoakSample(1.0, 100.0, None, 0, False, "DISCONNECTED")])
    assert rows.splitlines()[1].count(",") == 5


def test_should_stop_is_false_before_the_deadline():
    assert should_stop(now=10.0, started=0.0, hours=1.0) is False


def test_should_stop_is_true_after_the_deadline():
    assert should_stop(now=3601.0, started=0.0, hours=1.0) is True
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/test_soak.py -v
```

기대: `ModuleNotFoundError: No module named 'tools.soak'`.

- [ ] **Step 3: `tools/soak.py`를 구현한다**

```python
"""오래 돌리며 메모리와 링크를 기록한다 (스펙 §11 1단계 통과 기준).

화면을 띄우지 않고, 팔을 ENGAGED 로 만들지도 않는다. 24시간 동안 팔이 움직이면
위험하고 의미도 없다. ALIGNING 으로 두고 **네트워크·영상·메모리**만 본다.
클램프와 추종오차는 이미 단위 테스트와 2단계 실물 검증으로 확인했다.

    python -m tools.soak --hours 24 --out soak.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass

from common.config import load_home_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SoakSample:
    t: float
    rss_mb: float
    rtt_ms: float | None
    lost: int
    video_ok: bool
    state: str


def format_samples(samples: list[SoakSample]) -> str:
    lines = ["t_s,rss_mb,rtt_ms,lost,video_ok,state"]
    for s in samples:
        rtt = "" if s.rtt_ms is None else f"{s.rtt_ms:.2f}"
        lines.append(
            f"{s.t:.1f},{s.rss_mb:.1f},{rtt},{s.lost},{int(s.video_ok)},{s.state}"
        )
    return "\n".join(lines) + "\n"


def should_stop(now: float, started: float, hours: float) -> bool:
    return (now - started) >= hours * 3600.0


def _rss_mb() -> float:
    """이 프로세스의 메모리 사용량 (MB). 측정할 수 없으면 -1.0.

    psutil 의존을 늘리지 않으려고 Windows API 를 직접 부른다. 다른 OS 나 예외
    상황에서는 -1.0 을 돌려준다 - **메모리 측정 실패로 소킹 자체가 죽으면 안 된다.**
    """
    try:
        return _rss_mb_windows()
    except Exception:
        return -1.0


def _rss_mb_windows() -> float:
    import ctypes
    import ctypes.wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("PageFaultCount", ctypes.wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / (1024 * 1024)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="soak the client without a window")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between samples")
    parser.add_argument("--out", default="soak.csv")
    parser.add_argument("--cameras", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    from home.client import CommandState, ControlLink, LeaderSender, build_leader
    from home.video_recv import VideoClient

    cfg = load_home_config(args.config)
    leader = build_leader(cfg)
    connect = getattr(leader, "connect", None)
    if callable(connect):
        connect()

    link = ControlLink(host=cfg.server_host, port=cfg.control_port)
    video = VideoClient(host=cfg.server_host, port=cfg.video_port)
    # 클러치를 절대 누르지 않으므로 팔은 ALIGNING 에 머문다.
    sender = LeaderSender(link=link, leader=leader, commands=CommandState(), rate_hz=60.0)

    link.start()
    if args.cameras:
        video.start()
    sender.start()

    started = time.monotonic()
    samples: list[SoakSample] = []
    print(f"soaking for {args.hours} h, sampling every {args.interval:.0f}s -> {args.out}")
    try:
        while not should_stop(time.monotonic(), started, args.hours):
            time.sleep(args.interval)
            got = link.latest_telemetry()
            samples.append(
                SoakSample(
                    t=time.monotonic() - started,
                    rss_mb=_rss_mb(),
                    rtt_ms=link.rtt_ms,
                    lost=link.lost_packets,
                    video_ok=video.connected if args.cameras else False,
                    state=got[0].state.name if got else "NONE",
                )
            )
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(format_samples(samples))
    except KeyboardInterrupt:
        print("\nstopped early")
    finally:
        sender.stop()
        video.stop()
        link.stop()
        leader.close()

    if samples:
        first, last = samples[0], samples[-1]
        print(f"\nran {last.t / 3600:.1f} h, {len(samples)} samples")
        print(f"  lost packets: {last.lost}")
        bad = [s for s in samples if s.state not in ("ALIGNING", "HOMING")]
        print(f"  samples not in ALIGNING: {len(bad)}")

        if first.rss_mb < 0 or last.rss_mb < 0:
            print("  memory could not be measured on this platform")
        else:
            growth = last.rss_mb - first.rss_mb
            print(f"  memory {first.rss_mb:.0f} -> {last.rss_mb:.0f} MB  (growth {growth:+.0f} MB)")
            if growth > 100:
                print("  MEMORY GREW MORE THAN 100 MB - investigate before unattended operation")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m pytest tests/ 2>&1 | tail -3
```

기대: 전체 통과.

- [ ] **Step 5: 10분으로 먼저 돌려본다**

24시간 전에 짧게 돌려 도구 자체가 멀쩡한지 본다. 서버가 떠 있어야 한다.

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.soak --hours 0.17 --interval 20 --out soak-10min.csv
```

기대: `soak-10min.csv` 에 30줄쯤 쌓이고, `state` 가 계속 `ALIGNING`, 메모리 증가가 거의 없다.

- [ ] **Step 6: 24시간 돌린다**

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && "C:/Users/flash/miniconda3/envs/lerobot/python.exe" -m tools.soak --hours 24 --out soak-24h.csv
```

**작업대 PC 의 서버도 24시간 켜져 있어야 한다.** 두 PC 모두 절전으로 들어가지 않게 전원 설정을 확인한다.

판정 기준:
- 두 프로세스 모두 살아 있음
- 메모리 증가 100MB 미만
- `state` 가 계속 `ALIGNING`
- 서버 로그에 예기치 않은 `watchdog fired` 가 없음

- [ ] **Step 7: 결과를 문서에 남기고 커밋**

`docs/hardware-setup.md` 의 미결 항목에서 24시간 소킹을 지우고 결과를 적는다.

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation" && git add -A && git commit -m "feat: add soak runner and record the 24h result" && git push
```

---

## 3단계 완료 기준 (스펙 §11)

| 기준 | 확인 |
|---|---|
| LAN RTT < 20ms | Task 5 Step 4 |
| 영상 3채널 정상 | Task 6 Step 5 |
| 랜선 분리 시 200ms 내 HOLD | Task 5 Step 5 |
| 재연결 후 자동으로 움직이지 않음 | Task 5 Step 5 |
| 호밍 실물 동작 (2단계 이월) | Task 5 Step 6 |
| 카메라 1대 실패 격리 | Task 6 Step 6 |
| 24시간 연속 실행 (1단계 이월) | Task 7 Step 6 |

---

## 4단계로 넘길 항목

- 작업대 공유기에 **포트포워딩** (UDP 5555, TCP 5556) + 고정 IP
- `config/home.yaml` 의 `server_host` 를 공인 IP 로
- 원격 사용자 PC 세팅 (`docs/deployment.md` 의 "원격 사용자 PC 추가 절차")
- 리더 2대 캘리브레이션 — **작업대의 팔로워와 같은 기준 자세로.** `wrist_roll` 은 그리퍼 회전 방향까지 맞춰야 한다
- 인터넷 RTT < 150ms 측정, 30분 연속 조작
- 실제 센서 영상의 업로드 대역폭 재확인

## 미리 알아둘 위험

**캘리브레이션 파일이 git 에 없다는 것이 가장 흔한 실패다.** 팔을 옮기면 파일도 옮겨야 한다. Task 4 Step 7~8 을 건너뛰면 정렬 막대가 영원히 초록이 되지 않는데, 원인이 네트워크처럼 보여 엉뚱한 곳을 파게 된다.

**두 PC 의 커밋이 다르면** `check_link` 가 `reply did not parse` 로 알려준다. 그 메시지를 보면 즉시 `git pull` 을 떠올릴 수 있게 문구를 정했다.

**작업대 PC 의 네트워크 프로필이 "공용"이면** `-Profile Private` 방화벽 규칙이 적용되지 않는다. `check_link` 가 `no reply` 로 나오는데 서버는 멀쩡히 떠 있는 상황이면 이것부터 의심한다.
