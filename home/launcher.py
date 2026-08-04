"""exe 의 진입점. 원격 사용자가 보는 유일한 화면이다.

파이썬이 없는 PC 에서 exe 를 받아 실행하면 이 메뉴가 뜬다. 명령줄 인자도, 설치도,
캘리브레이션 파일 복사도 필요 없다 - 캘리브레이션은 서보 안에 있고, 나머지는 exe
옆의 `home.yaml` 하나에 들어 있다.

**순서가 곧 문제 해결 순서다.** 1번(내 팔) → 2번(회선과 작업대) → 3번(조종). 위에서
막히면 아래는 볼 것도 없다. "안 움직여요" 라는 연락을 받았을 때 그 사람이 스스로
어디까지 되는지 말할 수 있게 하는 것이 목적이다.
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from common.config import ConfigError, HomeConfig, load_home_config

log = logging.getLogger(__name__)

CONFIG_NAME = "home.yaml"

CONFIG_TEMPLATE = """\
# SO-101 원격 조종 설정. exe 와 같은 폴더에 두세요.
#
# server_host 는 작업대 PC 의 주소입니다. 관리자에게 받으세요.
server_host: "CHANGE-ME"
control_port: 5555
video_port: 5556

use_mock: false
client_watchdog_ms: 300

# 화면에 띄울 카메라 칸 수, 클러치 방식(toggle 또는 hold).
cameras: 2
clutch: toggle

# 리더 암 2대의 USB 시리얼 번호. 메뉴 4번(Show serial ports)으로 확인하세요.
# calibration_id 는 이 프로그램에서는 쓰이지 않지만 (캘리브레이션은 서보 안에
# 들어 있습니다) 어느 팔인지 적어두는 이름으로 남겨 둡니다.
arms:
  left:  { serial_number: "CHANGE-ME", calibration_id: "leader_left" }
  right: { serial_number: "CHANGE-ME", calibration_id: "leader_right" }
"""


def _app_dir() -> Path:
    """설정 파일을 찾을 곳.

    PyInstaller 로 묶으면 코드는 임시 폴더에 풀리므로 `__file__` 옆이 아니라
    **exe 가 놓인 폴더**를 봐야 한다. 사용자는 exe 옆에 설정을 둔다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def find_config(explicit: str | None = None) -> Path:
    """설정 파일 경로. 없으면 본보기를 만들어 주고 멈춘다."""
    if explicit:
        return Path(explicit)

    candidates = [
        _app_dir() / CONFIG_NAME,
        _app_dir() / "config" / "home.yaml",
        Path.cwd() / "config" / "home.yaml",
    ]
    for path in candidates:
        if path.is_file():
            return path

    target = _app_dir() / CONFIG_NAME
    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    raise ConfigError(
        f"no settings file found, so a template was created:\n\n    {target}\n\n"
        "Open it in Notepad, fill in server_host and the two serial numbers,\n"
        "then run this program again."
    )


# --- 메뉴 항목 -------------------------------------------------------------------


def check_arms(cfg: HomeConfig) -> None:
    from home.diagnose import diagnose_arms, format_arm_report

    if not cfg.arms:
        print("The settings file has no 'arms' section.")
        return

    print("Reading the leader arms. Nothing is changed - torque is left as it is.\n")
    reports = diagnose_arms(cfg.arms)
    for report in reports:
        print(format_arm_report(report))
        print()

    if all(r.ok for r in reports):
        print("Both arms answer on all six motors.")
    else:
        print("At least one motor did not answer. Check power and the cable that")
        print("daisy-chains the motors - the chain ends at the gripper (id 6).")


def check_connection(cfg: HomeConfig) -> None:
    from home.diagnose import format_video_report, survey_video
    from tools.check_link import check_control

    print(f"Checking {cfg.server_host} ...\n")

    control = check_control(cfg.server_host, cfg.control_port)
    print(f"  control  {'OK  ' if control.ok else 'FAIL'}  {control.detail}")
    if not control.ok:
        print()
        print("  Without the control channel nothing else matters. Ask whether the")
        print("  workbench PC has the server running.")
        return

    print()
    video = survey_video(cfg.server_host, cfg.video_port, seconds=3.0)
    print(format_video_report(video, expected=cfg.cameras or None))


def start_teleoperation(cfg_path: Path, cfg: HomeConfig) -> None:
    from home.client import main as client_main

    print("Starting. A window will open.")
    print("  SPACE  engage / release the clutch")
    print("  R      reset after a HOLD (hold it for 3 seconds)")
    print("  ESC    quit - the arm stops on its own a moment later")
    print()
    client_main(
        [
            "--config",
            str(cfg_path),
            "--cameras",
            str(cfg.cameras),
            "--clutch",
            cfg.clutch,
        ]
    )


def show_ports() -> None:
    from common.serial_ports import describe_ports

    print("Serial ports on this PC:\n")
    print(describe_ports())
    print()
    print("Copy the serial numbers of the two leader arms into the settings file.")
    print("To tell them apart, unplug one and run this again.")


# --- 메뉴 -----------------------------------------------------------------------

MENU = """\
  1  Check my arms          (are both arms plugged in, calibrated, torque off?)
  2  Check the connection   (can I reach the workbench? are its cameras sending?)
  3  Start teleoperation
  4  Show serial ports      (to fill in the settings file)
  q  Quit\
"""


def run_menu(cfg_path: Path) -> int:
    while True:
        try:
            cfg = load_home_config(cfg_path)
        except ConfigError as exc:
            print(f"\nThe settings file has a problem:\n  {exc}\n")
            input("Fix it, then press Enter to reload (or close this window). ")
            continue

        print()
        print("=" * 68)
        print("  SO-101 remote teleoperation")
        print(f"  workbench: {cfg.server_host}    settings: {cfg_path}")
        print("=" * 68)
        print(MENU)
        try:
            choice = input("\n  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 0

        print()
        try:
            if choice == "1":
                check_arms(cfg)
            elif choice == "2":
                check_connection(cfg)
            elif choice == "3":
                start_teleoperation(cfg_path, cfg)
            elif choice == "4":
                show_ports()
            elif choice in ("q", "quit", "exit"):
                return 0
            else:
                print("Type 1, 2, 3, 4 or q.")
                continue
        except KeyboardInterrupt:
            print("\nStopped.")
        except Exception:
            # 창이 닫혀버리면 원격 사용자는 아무것도 전할 수 없다. 무엇이 터졌는지
            # 화면에 남기고 메뉴로 돌아간다.
            print("\nSomething went wrong:\n")
            traceback.print_exc()
            print("\nCopy the text above when you report this.")

        input("\nPress Enter to go back to the menu. ")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    explicit = None
    if "--config" in argv:
        explicit = argv[argv.index("--config") + 1]

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    try:
        cfg_path = find_config(explicit)
    except ConfigError as exc:
        print(f"\n{exc}\n")
        input("Press Enter to close. ")
        return 1

    try:
        return run_menu(cfg_path)
    except Exception:
        traceback.print_exc()
        input("\nPress Enter to close. ")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
