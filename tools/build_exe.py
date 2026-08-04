"""exe 를 만들고, **정말로 새로 만들어졌고 실행되는지**까지 확인한다.

    python -m tools.build_exe

PyInstaller 를 직접 부르면 두 가지 방식으로 조용히 속는다.

1. **exe 가 실행 중이면 덮어쓰기가 막힌다** (WinError 5). PyInstaller 는 그 오류를
   stderr 로만 뱉으므로, 표준출력만 보고 있으면 성공한 줄 알고 **옛 exe 를 계속
   테스트하게 된다.** 실측 2026-08-04: 그렇게 10분을 날렸다.
2. **빌드 성공은 동작을 뜻하지 않는다.** 제외 목록을 잘못 잡으면 빌드는 멀쩡히
   끝나고 실행할 때 죽는다. 같은 날 두 번 그랬다 (libexpat.dll, jaraco).

그래서 여기서 셋을 다 한다: 실행 중인지 확인 → 빌드 → **띄워 본다.**
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "teleop.spec"
EXE_NAME = "so101-teleop.exe"


def running_instances() -> list[int]:
    """같은 이름의 exe 가 떠 있는지. 떠 있으면 빌드가 덮어쓰지 못한다."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == EXE_NAME.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def smoke_test(exe: Path) -> tuple[bool, str]:
    """설정이 없는 빈 폴더에서 띄워 본다.

    본보기를 만들고 안내하며 끝나는 것이 정상 동작이다. 여기까지 왔다는 것은
    부트로더, 런타임 훅, DLL 이 다 붙었다는 뜻이다 - 지금까지 실패는 전부 그
    앞에서 났다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = subprocess.run(
                [str(exe)],
                cwd=tmp,
                input="\n",
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return False, "the exe did not finish within 120s"

        output = result.stdout + result.stderr
        if "Traceback" in output and "template was created" not in output:
            return False, f"the exe crashed on startup:\n{output.strip()}"
        if "template was created" not in output:
            return False, f"unexpected first-run output:\n{output.strip()}"
        return True, "starts and writes a settings template"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the standalone exe and verify it runs")
    parser.add_argument("--skip-smoke", action="store_true", help="build only, do not run it")
    args = parser.parse_args(argv)

    exe = ROOT / "dist" / EXE_NAME

    pids = running_instances()
    if pids:
        print(f"{EXE_NAME} is still running (pid {', '.join(map(str, pids))}).")
        print("Windows will not let the build overwrite it, and PyInstaller reports that")
        print("only on stderr - you would end up testing the old exe. Close it first:")
        print(f"    Stop-Process -Name {EXE_NAME.removesuffix('.exe')} -Force")
        return 1

    before = exe.stat().st_mtime if exe.exists() else 0.0

    print(f"building from {SPEC.relative_to(ROOT)} ...")
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(ROOT / "build"),
            str(SPEC),
        ],
        cwd=ROOT,
        capture_output=True,  # stderr 까지 잡는다. 실패는 거기로만 나온다.
        text=True,
    )
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        print(f"\nPyInstaller failed (exit {result.returncode}):\n")
        print((result.stdout + result.stderr)[-3000:])
        return 1

    if not exe.exists():
        print(f"\nPyInstaller reported success but {exe} does not exist.")
        return 1

    if exe.stat().st_mtime <= before:
        # 성공을 보고했는데 파일이 그대로면 우리가 보고 있는 것은 옛 exe 다.
        print(f"\nPyInstaller reported success but {EXE_NAME} was not replaced.")
        print("Something is holding the file open. Do not trust any test you run now.")
        return 1

    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"built in {elapsed:.0f}s  ->  {exe}  ({size_mb:.1f} MB)")

    if args.skip_smoke:
        print("\nSmoke test skipped. A build that finishes is not a build that runs.")
        return 0

    print("\nsmoke test: starting the exe in an empty folder ...")
    ok, detail = smoke_test(exe)
    if not ok:
        print(f"  FAILED: {detail}")
        return 1
    print(f"  OK: {detail}")

    print("\nNext, run it yourself and go through the menu - the smoke test only proves")
    print("it starts. Menu 3 (teleoperation) opens a window and needs the workbench PC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
