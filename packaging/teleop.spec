# -*- mode: python ; coding: utf-8 -*-
"""파이썬이 없는 PC 에서 돌아가는 단일 exe 를 만든다.

    python -m tools.build_exe

**exclude 목록이 핵심이다.** lerobot 을 걷어냈지만 PyInstaller 는 import 그래프를
따라가다 무엇이든 주워 담을 수 있고, torch 하나만 들어와도 exe 가 4GB 가 된다.
여기서 명시적으로 막고, `tests/test_build.py` 가 import 단계에서 한 번 더 막는다.
두 겹인 이유는 어느 한쪽만으로는 조용히 새기 때문이다.

**단일 파일(onefile)로 만든다.** 원격 사용자는 받아서 두 번 누르는 것이 전부여야
한다. 대가로 실행할 때마다 임시 폴더에 푸느라 첫 화면까지 몇 초 걸린다 - 폴더째
배포하면 빠르지만, 파일이 수백 개라 "어느 것을 눌러야 하죠" 라는 연락이 온다.
"""

import os
import sys

ROOT = os.path.dirname(SPECPATH)  # noqa: F821  (PyInstaller 가 넣어준다)

# conda 는 네이티브 DLL 을 `Library/bin` 에 둔다. 표준 위치가 아니라 PyInstaller 가
# 의존성을 따라가다 못 찾고, 그러면 **빌드는 성공하는데 실행이 안 되는** exe 가
# 나온다 (실측: libexpat.dll 이 빠져 pyexpat import 에서 죽었다).
#
# 빠진 DLL 을 하나씩 손으로 적지 않고 검색 경로를 알려 준다. 손으로 적으면 다음에
# 다른 것이 빠졌을 때 또 같은 방식으로 죽는다.
_conda_bin = os.path.join(sys.prefix, "Library", "bin")
if os.path.isdir(_conda_bin):
    os.environ["PATH"] = _conda_bin + os.pathsep + os.environ.get("PATH", "")

# 넣으면 안 되는 것들. lerobot 과 torch 는 4.2GB 짜리 이유이고, 나머지는 어쩌다
# 딸려 들어오는 큰 덩어리들이다.
EXCLUDE = [
    "lerobot",
    "torch",
    "torchvision",
    "pandas",
    "scipy",
    "matplotlib",
    "datasets",
    "transformers",
    "huggingface_hub",
    "IPython",
    "notebook",
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "pytest",
    # setuptools 는 **빼면 안 된다.** pygame 이 pkg_resources 를 import 하고,
    # PyInstaller 는 그것 때문에 런타임 훅을 넣는다. setuptools 를 잘라내면 그 훅이
    # 자기가 쓸 것(jaraco 등)을 못 찾아 exe 가 아예 시작하지 못한다.
    # 실측: 두 번 연속으로 "빌드는 성공, 실행은 실패" 를 만들었다.
]

a = Analysis(
    [os.path.join(ROOT, "home", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # 문자열로만 참조되거나 늦게 import 되어 그래프에 안 잡히는 것들.
        "scservo_sdk",
        "serial.tools.list_ports",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDE,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="so101-teleop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 로 줄이면 백신이 더 자주 잡는다. 배포처가 3명뿐이라 크기보다 중요.
    console=True,  # 메뉴가 콘솔이다. 끄면 진단 결과를 볼 수 없다.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
