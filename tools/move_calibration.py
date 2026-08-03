r"""캘리브레이션 파일을 PC 사이로 옮긴다.

**이 파일들은 git 저장소에 없다.** lerobot 이
``%USERPROFILE%\.cache\huggingface\lerobot\calibration\`` 에 저장한다.
따라서 폴더를 아무리 동기화해도 따라가지 않는다.

팔로워 2대가 작업대 PC 로 물리적으로 옮겨가므로 그 팔의 캘리브레이션도 같이 가야
한다. 안 가면 작업대 PC 가 캘리브레이션 없이 연결을 시도하고, 관절각이 전혀 다른
값으로 읽혀 정렬이 영원히 맞지 않는다. 증상이 네트워크 문제처럼 보여 엉뚱한 곳을
파게 되는 것이 이 도구를 만든 이유다.

    # 개발 PC 에서 - 팔로워 2대 것만 내보낸다
    python -m tools.move_calibration --export follower_left follower_right --to D:\cal

    # 작업대 PC 에서
    python -m tools.move_calibration --import-from D:\cal
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
    matches = list(root.rglob(f"{cid}.json"))
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
    root = calibration_root() if root is None else Path(root)
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
    root = calibration_root() if root is None else Path(root)
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
    root = calibration_root() if root is None else Path(root)
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
        print("  python -m tools.move_calibration --import-from <folder>")
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
