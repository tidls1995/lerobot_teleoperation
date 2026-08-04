"""lerobot 없는 읽기가 lerobot 과 같은 숫자를 내는지 **실물에서** 대조한다.

단위 테스트(`tests/test_feetech_lite.py`)는 변환 공식을 lerobot 과 직접 비교하지만,
그것은 계산만 검증한다. 실제로 모터에서 값을 꺼내는 경로 - sync read, 부호-크기 해제,
서보 EEPROM 에서 읽은 캘리브레이션 - 가 같은 결과를 주는지는 팔을 물려봐야 안다.

**여기서 안 맞으면 exe 작업을 진행하면 안 된다.** 서버(팔로워)는 계속 lerobot 을
쓰므로, 리더 쪽 각도가 다르면 정렬이 끝나지 않거나 어긋난 채로 조종이 시작된다.

    python -m tools.compare_read --config config/home.yaml --kind leader

**대조하는 동안 팔을 건드리지 마라.** 두 방식이 같은 포트를 동시에 열 수 없어 차례로
읽으므로, 그 사이에 팔이 움직이면 차이가 그것 때문인지 계산 때문인지 알 수 없다.
"""

from __future__ import annotations

import argparse
import logging
import time

from common.feetech_lite import (
    MOTOR_NAMES,
    FeetechLiteBus,
    looks_uncalibrated,
    raw_to_degrees,
    raw_to_percent,
)
from common.joints import ARM_SIDES
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)

#: 몇 도까지 같다고 볼 것인가.
#:
#: 1틱이 360/4095 = 0.088도다. 사람이 안 건드려도 서보 읽기는 1~2틱 흔들리므로
#: 그만큼은 허용한다. 계산이 틀렸다면 차이는 이보다 훨씬 크게 나온다.
TOLERANCE_DEG = 0.3
TOLERANCE_PCT = 0.5

#: 흔들림을 줄이려고 여러 번 읽어 중앙값을 쓴다.
SAMPLES = 15


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def read_with_feetech_lite(port: str) -> tuple[dict[str, float], dict[int, object], float]:
    """lerobot 없이 읽는다. 캘리브레이션도 서보에서 직접 가져온다."""
    bus = FeetechLiteBus(port=port)
    bus.connect()
    try:
        calibration = bus.read_calibration()
        started = time.monotonic()
        samples: dict[int, list[int]] = {i: [] for i in bus.ids}
        for _ in range(SAMPLES):
            for motor_id, raw in bus.sync_read_positions().items():
                samples[motor_id].append(raw)
        hz = SAMPLES / (time.monotonic() - started)

        out: dict[str, float] = {}
        for index, name in enumerate(MOTOR_NAMES):
            motor_id = index + 1
            raw = int(_median([float(v) for v in samples[motor_id]]))
            cal = calibration[motor_id]
            out[name] = raw_to_percent(raw, cal) if name == "gripper" else raw_to_degrees(raw, cal)
        return out, calibration, hz
    finally:
        bus.close()


def read_with_lerobot(port: str, calibration_id: str) -> tuple[dict[str, float], float]:
    from lerobot.teleoperators.so_leader import SOLeader, SOLeaderTeleopConfig

    teleop = SOLeader(SOLeaderTeleopConfig(port=port, id=calibration_id, use_degrees=True))
    teleop.connect(calibrate=False)
    try:
        teleop.bus.disable_torque(num_retry=5)
        started = time.monotonic()
        samples: dict[str, list[float]] = {name: [] for name in MOTOR_NAMES}
        for _ in range(SAMPLES):
            action = teleop.get_action()
            for key, value in action.items():
                samples[key.removesuffix(".pos")].append(float(value))
        hz = SAMPLES / (time.monotonic() - started)
        return {name: _median(values) for name, values in samples.items()}, hz
    finally:
        teleop.disconnect()


def compare_arm(side: str, port: str, calibration_id: str) -> bool:
    print(f"\n{'=' * 62}")
    print(f"{side} arm on {port} (calibration id {calibration_id})")
    print("=" * 62)

    # feetech_lite 를 **먼저** 읽는다. lerobot 은 연결할 때 캘리브레이션 파일을
    # 서보에 쓸 수 있어서, 나중에 읽으면 EEPROM 이 파일과 같아진 뒤를 보게 된다.
    # exe 가 실제로 마주할 상태는 손대기 전의 EEPROM 이다.
    lite_values, calibration, lite_hz = read_with_feetech_lite(port)
    lerobot_values, lerobot_hz = read_with_lerobot(port, calibration_id)

    print(f"\ncalibration read from the servos (no file needed)")
    print("  motor              id   homing   min    max")
    suspicious = []
    for index, name in enumerate(MOTOR_NAMES):
        cal = calibration[index + 1]
        mark = ""
        if looks_uncalibrated(cal):
            mark = "  <-- looks never calibrated"
            suspicious.append(name)
        print(
            f"  {name:<16} {cal.id:>4} {cal.homing_offset:>8} {cal.range_min:>6} "
            f"{cal.range_max:>6}{mark}"
        )

    print(f"\nread rate: feetech_lite {lite_hz:.0f} Hz, lerobot {lerobot_hz:.0f} Hz")

    print("\njoint values")
    print("  motor            feetech_lite      lerobot        difference")
    ok = True
    for name in MOTOR_NAMES:
        unit = "%" if name == "gripper" else "deg"
        tolerance = TOLERANCE_PCT if name == "gripper" else TOLERANCE_DEG
        mine = lite_values[name]
        theirs = lerobot_values[name]
        diff = mine - theirs
        flag = ""
        if abs(diff) > tolerance:
            flag = f"  <-- MISMATCH (over {tolerance} {unit})"
            ok = False
        print(f"  {name:<16} {mine:>10.3f} {theirs:>13.3f} {diff:>13.3f} {unit}{flag}")

    if suspicious:
        print(f"\n  note: {', '.join(suspicious)} look uncalibrated - run lerobot's")
        print("  calibration for this arm before using it.")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="check that reading without lerobot gives the same numbers"
    )
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--kind", choices=["leader", "follower"], default="leader")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")

    if args.kind == "leader":
        from common.config import load_home_config

        cfg = load_home_config(args.config)
    else:
        from common.config import load_workbench_config

        cfg = load_workbench_config(args.config)

    if not cfg.arms:
        print(f"{args.config} has no 'arms' section")
        return 1

    print("Do not touch the arms while this runs - the two methods read one after")
    print("the other, so movement in between would look like a mismatch.")

    # 포트를 하나라도 열기 전에 전부 조회한다 (실물 어댑터와 같은 이유).
    ports = {
        side: resolve_port_spec(cfg.arms[side].serial_number, cfg.arms[side].port)
        for side in ARM_SIDES
        if side in cfg.arms
    }

    all_ok = True
    for side, port in ports.items():
        all_ok &= compare_arm(side, port, cfg.arms[side].calibration_id)

    print(f"\n{'=' * 62}")
    if all_ok:
        print("Every joint matches. Reading without lerobot is equivalent, so the")
        print("exe can drop lerobot (and its 4.2 GB of torch).")
        return 0
    print("MISMATCH. Do not build the exe on this - the follower uses lerobot, so a")
    print("different angle here means alignment never completes, or worse, teleoperation")
    print("starts from a wrong pose.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
