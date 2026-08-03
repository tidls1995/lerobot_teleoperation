"""서버 기동 순서를 한 단계씩 재현하며 매번 시리얼 포트 열거를 시도한다.

**왜 필요한가 (2026-08-03, 작업대 PC):**

``python -m workbench.server`` 가 포트 조회에서 ``[WinError 87]`` 로 4/4 실패하는데,
같은 순간 ``probe_hardware --ports`` 는 3/3 성공했다. 다음이 전부 무죄로 배제됐다:

    cv2, numpy, 소켓 bind, SO_EXCLUSIVEADDRUSE, 마지막 에러값 오염,
    lerobot.motors.feetech, lerobot.robots.so_follower, 우리 어댑터 모듈

즉 import 가 아니라 **실행 순서**의 문제다. 한 줄씩 추측하는 대신 기동 순서를
그대로 밟으며 어느 단계 다음부터 깨지는지 찾는다.

    python -m tools.probe_startup
"""

from __future__ import annotations

import argparse
import sys


def _try_enumerate(label: str) -> bool:
    """지금 시점에 포트를 열거할 수 있는지. 실패해도 계속 진행한다."""
    from serial.tools import list_ports

    try:
        ports = list(list_ports.comports())
    except Exception as exc:
        print(f"  [FAIL] {label:38s} {type(exc).__name__}: {exc}")
        return False
    print(f"  [ok  ] {label:38s} {len(ports)} port(s)")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="find which startup step breaks port enumeration")
    parser.add_argument("--config", default="config/workbench.yaml")
    args = parser.parse_args(argv)

    print("replaying the server startup, enumerating ports after each step\n")
    first_failure: str | None = None

    def check(label: str) -> None:
        nonlocal first_failure
        if not _try_enumerate(label) and first_failure is None:
            first_failure = label

    check("0. baseline (nothing done yet)")

    import logging

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    check("1. logging.basicConfig(INFO)")

    from common.config import load_workbench_config

    cfg = load_workbench_config(args.config)
    check("2. load_workbench_config")

    from workbench.server import build_server

    server, publishers = build_server(cfg)
    check("3. build_server (no hardware touched)")

    for pub in publishers:
        pub.start()
    check(f"4. started {len(publishers)} camera publisher(s)")

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    check("5. socket() created")

    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    check("6. setsockopt SO_EXCLUSIVEADDRUSE")

    try:
        sock.bind(("0.0.0.0", cfg.control_port))
    except OSError as exc:
        print(f"\n  cannot bind udp/{cfg.control_port}: {exc}")
        print("  Another server is probably still running. Stop it and try again.")
        sock.close()
        for pub in publishers:
            pub.stop()
        return 1
    check(f"7. bind udp/{cfg.control_port}")

    sock.settimeout(0.005)
    check("8. settimeout(0.005)")

    # 서버가 실제로 하는 다음 동작. 여기까지 왔는데 위가 다 ok 라면
    # 문제는 조회가 아니라 그 이후에 있다.
    from common.serial_ports import resolve_port_spec

    for side in ("left", "right"):
        arm = cfg.arms[side]
        try:
            port = resolve_port_spec(arm.serial_number, arm.port)
            print(f"  [ok  ] 9. resolve {side:5s} -> {port}")
        except Exception as exc:
            print(f"  [FAIL] 9. resolve {side:5s} {type(exc).__name__}: {exc}")
            if first_failure is None:
                first_failure = f"9. resolve {side}"

    sock.close()
    for pub in publishers:
        pub.stop()

    print()
    if first_failure is None:
        print("every step succeeded - the failure is not in this sequence.")
        print("Run 'python -m workbench.server --config config/workbench.yaml' again;")
        print("if it still fails, the difference is elsewhere.")
        return 0
    print(f"first failure at: {first_failure}")
    print("That step is what breaks port enumeration.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
