"""wrist_roll 의 영점만 다시 잡는다.

**왜 필요한가**

lerobot 의 캘리브레이션은 wrist_roll 을 "한 바퀴 도는 관절"로 취급해 가동범위를
측정하지 않는다 (``range_min=0, range_max=4095``). 대신 영점은
``set_half_turn_homings()`` 가 **캘리브레이션 당시의 자세**로 정한다.

따라서 캘리브레이션할 때 리더와 팔로워의 그리퍼 회전 방향이 서로 달랐다면, 그
차이가 영점에 그대로 박힌다. 실측으로 양팔 모두 32~39도 어긋난 것이 확인되었고,
리더는 손잡이가 책상에 닿아 그 각도까지 돌릴 수 없어 정렬 절차를 통과할 수 없었다.

**무엇을 하는가**

팔 4대의 wrist_roll 을 물리적으로 같은 방향에 놓게 한 뒤, 그 자세를 각 팔의 영점으로
다시 쓴다. 다른 관절 5개는 건드리지 않는다.

**lerobot 의 함정**

``set_half_turn_homings(["wrist_roll"])`` 는 하드웨어에는 wrist_roll 만 쓰지만,
내부의 ``reset_calibration`` 이 마지막에 ``self.calibration = {}`` 로 **메모리
캘리브레이션을 통째로 비운다.** 그대로 두면 이후 정규화 읽기가
"has no calibration registered" 로 죽는다. 이 스크립트는 호출 전에 저장해두고
wrist_roll 의 homing_offset 만 갱신해 되돌려 놓는다.

    python -m tools.rezero_wrist_roll
    python -m tools.rezero_wrist_roll --dry-run    # 현재 어긋난 양만 보고 끝
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import time
from pathlib import Path

from common.config import load_home_config, load_workbench_config
from common.serial_ports import resolve_port_spec

log = logging.getLogger(__name__)

JOINT = "wrist_roll"


def _open_arms():
    """리더 2대 + 팔로워 2대를 열고 (라벨, 객체, 버스) 목록을 돌려준다.

    팔로워는 손으로 돌려야 하므로 토크를 끈다.
    """
    from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig
    from lerobot.teleoperators.so_leader import SOLeader, SOLeaderTeleopConfig

    home = load_home_config("config/home.yaml")
    workbench = load_workbench_config("config/workbench.yaml")

    opened = []
    for side, arm in sorted(home.arms.items()):
        port = resolve_port_spec(arm.serial_number, arm.port)
        dev = SOLeader(SOLeaderTeleopConfig(port=port, id=arm.calibration_id, use_degrees=True))
        dev.connect(calibrate=False)
        dev.bus.disable_torque(num_retry=5)
        opened.append((f"leader {side}", dev, dev.bus, port))

    for side, arm in sorted(workbench.arms.items()):
        port = resolve_port_spec(arm.serial_number, arm.port)
        dev = SOFollower(
            SOFollowerRobotConfig(
                port=port,
                id=arm.calibration_id,
                use_degrees=True,
                max_relative_target=None,
                disable_torque_on_disconnect=True,
                cameras={},
            )
        )
        dev.connect(calibrate=False)
        # 손으로 돌려야 한다. lerobot 의 configure() 가 토크를 켜 두므로 끈다.
        dev.bus.disable_torque(num_retry=5)
        opened.append((f"follower {side}", dev, dev.bus, port))

    return opened


def _read_joint(bus) -> float:
    return float(bus.sync_read("Present_Position", [JOINT])[JOINT])


def _backup(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    return backup


def rezero(dry_run: bool) -> int:
    opened = _open_arms()
    try:
        print(f"\ncurrent {JOINT} (degrees):")
        for label, _dev, bus, port in opened:
            print(f"  {label:16s} {port:6s} {_read_joint(bus):8.1f}")

        values = [_read_joint(bus) for _, _, bus, _ in opened]
        spread = max(values) - min(values)
        print(f"\nspread across the four arms: {spread:.1f} deg")
        print("(the alignment threshold is 3.0 deg, so anything above that blocks ENGAGED)")

        if dry_run:
            print("\ndry run - nothing was written")
            return 0

        print(f"\nPut all four {JOINT} joints at the SAME visual orientation.")
        print()
        print("  1. Pick an orientation the LEADER arms can hold comfortably - one where")
        print("     the handle is clear of the desk. That handle is what limits the range,")
        print("     so the leader chooses the reference, not the follower.")
        print("  2. Turn both followers by hand to match it visually. Their torque is off.")
        print("  3. Sight down the forearm of each arm and check the gripper jaws point")
        print("     the same way on all four.")
        print()
        print("Whatever orientation you pick becomes 0 degrees for all four arms, so it")
        print("only has to be reachable - it does not have to be any particular angle.")
        answer = input("\nType 'yes' when all four match, or anything else to abort: ")
        if answer.strip().lower() != "yes":
            print("aborted - nothing was written")
            return 1

        for label, dev, bus, _port in opened:
            before = _read_joint(bus)

            # reset_calibration 이 self.calibration 을 통째로 비우므로 미리 저장한다.
            saved = dict(bus.calibration)
            offsets = bus.set_half_turn_homings([JOINT])
            new_offset = offsets[JOINT]

            saved[JOINT] = dataclasses.replace(saved[JOINT], homing_offset=int(new_offset))
            bus.calibration = saved
            dev.calibration = saved

            path = Path(dev.calibration_fpath)
            backup = _backup(path)
            dev._save_calibration()

            after = _read_joint(bus)
            print(
                f"  {label:16s} {before:8.1f} -> {after:8.1f}   "
                f"homing_offset={new_offset}  (backup {backup.name})"
            )

        time.sleep(0.2)
        print(f"\nverifying - all four should now read close to 0:")
        finals = []
        for label, _dev, bus, _port in opened:
            v = _read_joint(bus)
            finals.append(v)
            print(f"  {label:16s} {v:8.1f}")

        spread = max(finals) - min(finals)
        print(f"\nspread is now {spread:.1f} deg")
        if spread > 3.0:
            print("STILL ABOVE THE 3.0 deg THRESHOLD.")
            print("The four joints were probably not at the same orientation when you")
            print("confirmed. Run this again, more carefully.")
            return 1
        print("within the alignment threshold - the arms can now reach ENGAGED")
        return 0
    finally:
        for label, dev, _bus, _port in opened:
            try:
                dev.disconnect()
            except Exception:
                log.exception("%s: disconnect failed", label)


def verify_other_joints_survived() -> int:
    """다른 관절 5개의 캘리브레이션이 파일에 그대로 남아 있는지 확인한다."""
    from lerobot.utils.constants import HF_LEROBOT_CALIBRATION

    from common.joints import MOTOR_NAMES

    root = Path(HF_LEROBOT_CALIBRATION)
    files = sorted(root.rglob("*.json"))
    if not files:
        print("no calibration files found")
        return 1
    ok = True
    for p in files:
        if p.suffix == ".bak":
            continue
        cal = json.loads(p.read_text())
        missing = set(MOTOR_NAMES) - set(cal)
        if missing:
            print(f"{p.name}: MISSING {sorted(missing)}")
            ok = False
        else:
            print(f"{p.name}: all 6 motors present")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"re-zero the {JOINT} joint on all four arms")
    parser.add_argument("--dry-run", action="store_true", help="only report the current spread")
    parser.add_argument(
        "--verify-files", action="store_true", help="check that all six motors survive in the files"
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")

    if args.verify_files:
        return verify_other_joints_survived()
    return rezero(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
