"""리더 암의 현재 자세를 읽어 `home_pose` 설정 블록으로 출력한다.

**왜 리더에서 읽는가**

`home_pose` 는 HOLD 에서 리셋했을 때 팔로워를 되돌릴 자세다. 그 자세는 **리더가
도달할 수 있어야** 한다 - 되돌아간 뒤에 조종자가 리더를 거기에 맞춰야 정렬이
끝나기 때문이다.

팔로워에서 읽으면 리더가 도달 못 하는 자세를 목표로 삼을 수 있다. wrist_roll 에서
이미 겪었듯이, 리더는 손잡이가 책상에 닿아 못 가는 각도가 있다.

캘리브레이션이 4대 모두 일치하면 리더 각도와 팔로워 각도는 같은 물리 자세를
가리키므로, 리더에서 읽은 값을 그대로 팔로워 목표로 쓸 수 있다.

    python -m tools.capture_home_pose
"""

from __future__ import annotations

import argparse
import logging

from common.config import load_home_config, load_workbench_config
from common.joints import GRIPPER_INDICES
from common.protocol import JOINT_NAMES
from home.leader_arms import RealLeaderArms

log = logging.getLogger(__name__)


def capture(config_path: str, workbench_config_path: str) -> int:
    cfg = load_home_config(config_path)
    arms = RealLeaderArms(arms=cfg.arms)

    print("Put BOTH leader arms in the pose you want the followers to return to.")
    print()
    print("  - It must be a pose the leader can hold comfortably. After homing, you")
    print("    have to match the leader to it to finish aligning.")
    print("  - Somewhere near the middle of each joint's range is a good choice, so")
    print("    the arm is not near a limit when it gets there.")
    print("  - Keep the grippers part-way open rather than fully closed.")
    print()

    arms.connect()
    try:
        joints = arms.read_positions()
    finally:
        arms.close()

    limits = load_workbench_config(workbench_config_path).safety.joint_limits
    outside = []
    for i, name in enumerate(JOINT_NAMES):
        lo, hi = limits[name]
        if not (lo <= joints[i] <= hi):
            outside.append((name, joints[i], lo, hi))

    print("  home_pose:")
    for i, name in enumerate(JOINT_NAMES):
        unit = "percent" if i in GRIPPER_INDICES else "deg"
        print(f"    {name:22s} {joints[i]:8.1f}   # {unit}")

    if outside:
        print()
        print("PROBLEM - these joints are outside joint_limits, so the arm could never")
        print("reach them and homing would never finish:")
        for name, value, lo, hi in outside:
            print(f"  {name}: {value:.1f} not in [{lo}, {hi}]")
        print("Move the leader further inside its range and run this again.")
        return 1

    print()
    print("Paste the block above into the safety: section of config/workbench.yaml.")
    print("Values are already inside joint_limits.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="capture a home pose from the leader arms")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--workbench-config", default="config/workbench.yaml")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")
    return capture(args.config, args.workbench_config)


if __name__ == "__main__":
    raise SystemExit(main())
