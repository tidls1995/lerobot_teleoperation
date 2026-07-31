"""하드웨어를 하나씩 따로 점검하는 진단 스크립트.

조종 소프트웨어 전체를 띄우기 전에 이걸로 팔과 카메라를 개별 확인한다.
'안 움직인다'의 원인 후보를 서버·네트워크·안전로직에서 하드웨어로 좁히는 것이
목적이다.

    python -m tools.probe_hardware --ports
    python -m tools.probe_hardware --arms --kind leader   --config config/home.yaml
    python -m tools.probe_hardware --arms --kind follower --config config/workbench.yaml
    python -m tools.probe_hardware --cameras
"""

from __future__ import annotations

import argparse
import logging
import time

from common.joints import GRIPPER_INDICES
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
        print("  left arm ->                                           | right arm ->")
        deadline = time.monotonic() + 3.0
        started = time.monotonic()
        reads = 0
        last_print = 0.0
        while time.monotonic() < deadline:
            pos = arms.read_positions()
            reads += 1
            now = time.monotonic()
            if now - last_print > 0.5:
                last_print = now
                cells = []
                for i, v in enumerate(pos):
                    unit = "%" if i in GRIPPER_INDICES else " "
                    cells.append(f"{v:6.1f}{unit}")
                    if i == 5:
                        cells.append("|")
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
            size = cam.actual_size or (0, 0)
            print(f"index {index}: {size[0]}x{size[1]}  {frames} fps")
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
