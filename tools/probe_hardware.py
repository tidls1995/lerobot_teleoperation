"""하드웨어를 하나씩 따로 점검하는 진단 스크립트.

조종 소프트웨어 전체를 띄우기 전에 이걸로 팔과 카메라를 개별 확인한다.
'안 움직인다'의 원인 후보를 서버·네트워크·안전로직에서 하드웨어로 좁히는 것이
목적이다.

    python -m tools.probe_hardware --ports
    python -m tools.probe_hardware --arms --kind leader   --config config/home.yaml
    python -m tools.probe_hardware --arms --kind follower --config config/workbench.yaml
    python -m tools.probe_hardware --cameras
    python -m tools.probe_hardware --cameras-together --config config/workbench.yaml
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import threading
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


def scan_motors() -> int:
    """모든 팔의 모터 1~6이 응답하는지 개별로 확인한다.

    "There is no status packet!" 로 연결이 실패했을 때, 모터가 진짜 죽은 것인지
    그냥 패킷이 유실된 것인지 가른다. 여기서 전부 O 로 나오면 배선과 전원은
    정상이고 간헐적 유실이므로, 어댑터의 연결 재시도가 처리해준다.
    """
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    from common.joints import MOTOR_NAMES
    from common.serial_ports import list_serial_ports

    ports = list_serial_ports()
    if not ports:
        print("no serial ports found - are the arms plugged in?")
        return 1

    ok_everywhere = True
    for p in ports:
        motors = {n: Motor(i + 1, "sts3215", MotorNormMode.DEGREES) for i, n in enumerate(MOTOR_NAMES)}
        bus = FeetechMotorsBus(port=p.device, motors=motors)
        try:
            bus.connect(handshake=False)
        except Exception as exc:
            print(f"{p.device}: cannot open port - {exc}")
            ok_everywhere = False
            continue
        marks = []
        try:
            for name in MOTOR_NAMES:
                try:
                    answered = bus.ping(name, num_retry=2) is not None
                except Exception:
                    answered = False
                marks.append("O" if answered else "X")
                ok_everywhere = ok_everywhere and answered
        finally:
            bus.disconnect(disable_torque=False)
        cells = " ".join(f"{i + 1}:{m}" for i, m in enumerate(marks))
        print(f"{p.device} serial={p.serial_number}  {cells}")

    print()
    if ok_everywhere:
        print("every motor answered. Wiring and power are fine; a failed connect was")
        print("just a dropped packet, which the adapters retry.")
        return 0
    print("some motors did not answer. Check power and the daisy-chain cable to the")
    print("first motor marked X - the chain ends at the gripper (id 6).")
    return 1


def check_sides(config_path: str, kind: str, seconds: float) -> int:
    """한 팔만 움직였을 때 배열의 그 절반만 변하는지 확인한다.

    좌우가 뒤바뀐 채로 조종을 시작하면 조종자가 왼쪽을 움직였는데 오른쪽 팔이
    장비를 친다. 설정의 시리얼 번호가 뒤바뀌었는지 잡아내는 검증이다.
    """
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

    arms.connect()
    try:
        for side, expected in (("LEFT", slice(0, 6)), ("RIGHT", slice(6, 12))):
            other = slice(6, 12) if expected.start == 0 else slice(0, 6)
            input(f"\n>>> Move ONLY the {side} arm when you press ENTER ({seconds:.0f}s) ... ")
            start = arms.read_positions()
            deadline = time.monotonic() + seconds
            moved = [0.0] * len(start)
            while time.monotonic() < deadline:
                now_pos = arms.read_positions()
                for i, v in enumerate(now_pos):
                    moved[i] = max(moved[i], abs(v - start[i]))

            mine = max(moved[expected])
            theirs = max(moved[other])
            print(f"  {side} half moved at most {mine:6.1f}, other half {theirs:6.1f}")
            if mine < 3.0:
                print(f"  FAIL: the {side} arm barely moved - did you move it?")
                return 1
            if theirs > mine * 0.3:
                print(
                    f"  FAIL: the other half also moved. The two arms are probably swapped "
                    f"in the config, or you moved both."
                )
                return 1
            print(f"  OK: moving the {side} arm changes the {side} half of the array")
        print("\nleft/right mapping is correct")
    finally:
        arms.close()
    return 0


def snapshot_cameras(max_index: int, out_dir: str) -> int:
    """각 카메라 인덱스에서 한 장씩 찍어 파일로 저장한다.

    ``--cameras`` 는 인덱스와 해상도만 알려주지 그 카메라가 어디 달린 것인지는
    모른다. 렌즈를 하나씩 손으로 가려 보는 것보다 사진을 놓고 비교하는 편이 확실하다.
    노트북 내장 웹캠이 섞여 있는 경우도 여기서 바로 드러난다.
    """
    import cv2

    from workbench.usb_camera import CameraOpenError, UsbCamera

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    saved = []
    for index in range(max_index):
        # 해상도를 요청하지 않는다. 장치가 주는 그대로 찍어야 무엇인지 알아보기 쉽다.
        cam = UsbCamera(cam_id=index, name=f"index{index}", index=index, width=0, height=0, fps=15)
        try:
            cam.open()
        except CameraOpenError:
            continue
        try:
            # 첫 프레임은 노출이 안 맞아 어둡게 나오는 카메라가 많다. 몇 장 버린다.
            frame = None
            for _ in range(5):
                frame = cam.read()
            if frame is None:
                print(f"index {index}: opened but no frame")
                continue
            path = out / f"cam{index}.png"
            cv2.imwrite(str(path), frame)
            size = cam.actual_size or (0, 0)
            print(f"index {index}: {size[0]}x{size[1]}  ->  {path}")
            saved.append(index)
        finally:
            cam.close()

    if not saved:
        print(f"no cameras found on indices 0..{max_index - 1}")
        return 1
    print()
    print(f"open the files in {out.resolve()} and note which is which:")
    print("  front        - the whole workbench")
    print("  wrist_left   - on the left follower's wrist")
    print("  wrist_right  - on the right follower's wrist")
    print("A laptop's built-in webcam usually shows the room or your face.")
    return 0


def cameras_together(config_path: str, fourcc: str | None, seconds: float) -> int:
    """설정에 있는 카메라를 **서버와 똑같이 동시에** 열어 무엇이 실패하는지 본다.

    ``--cameras`` 는 한 대씩 열고 **닫은 뒤** 다음으로 넘어가므로, 동시에 유지할 때
    생기는 문제를 절대 재현하지 못한다. 실측(2026-08-03, 작업대 PC)에서 서버는
    3대 중 2대만 열었는데 ``--cameras`` 는 4대 전부 성공했다. 그 차이가 여기 있다.

    유력한 원인은 **USB 대역폭**이다. UVC 카메라가 비압축(YUY2)으로 스트리밍하면
    대역폭을 프레임 크기에 비례해 미리 예약해버려서, 같은 컨트롤러에 여러 대가
    붙으면 나중 것이 아예 열리지 않는다. 그래서 실제 협상된 포맷을 함께 찍는다 -
    YUY2 로 나오면 그 가설이고, ``--fourcc MJPG`` 로 다시 돌려 확인한다.

    **한 프로세스에서 한 번만 측정한다.** 같은 프로세스에서 포맷을 바꿔 두 번 재보면
    앞선 열기가 남긴 드라이버 상태가 뒤 결과를 오염시킨다 (전례: 진단이 스스로
    메인 스레드를 '예방주사' 놓아 모든 판정을 무효로 만든 일).
    """
    import cv2  # noqa: F401  # 서버와 같은 import 순서를 재현한다 (cv2 -> lerobot)

    import lerobot.motors.feetech  # noqa: F401

    from common.config import load_workbench_config
    from workbench.usb_camera import UsbCamera

    cfg = load_workbench_config(config_path)
    if not cfg.cameras:
        print(f"no cameras configured in {config_path}")
        return 1

    print(f"opening all {len(cfg.cameras)} cameras at once, like the server does")
    if fourcc:
        print(f"forcing pixel format {fourcc}")
    print()

    results: dict[int, dict] = {}
    lock = threading.Lock()
    stop = threading.Event()

    def work(cam_cfg) -> None:
        cam = UsbCamera(
            cam_id=cam_cfg.id,
            name=cam_cfg.name,
            index=cam_cfg.index,
            width=cam_cfg.width,
            height=cam_cfg.height,
            fps=cam_cfg.fps,
            fourcc=fourcc,
        )
        entry = results[cam_cfg.id]
        try:
            cam.open()
        except Exception as exc:
            with lock:
                entry["error"] = str(exc).split(". Run")[0]
            return
        with lock:
            entry["opened"] = True
            entry["size"] = cam.actual_size
            entry["fourcc"] = cam.actual_fourcc
        # 서버와 같은 주기로 읽는다. 최대 성능을 재는 것이 아니라 재현이 목적이다.
        interval = 1.0 / cam_cfg.fps
        try:
            while not stop.is_set():
                started = time.monotonic()
                if cam.read() is not None:
                    with lock:
                        entry["frames"] += 1
                stop.wait(max(0.0, interval - (time.monotonic() - started)))
        finally:
            cam.close()

    threads = []
    for c in cfg.cameras:
        results[c.id] = {
            "name": c.name,
            "index": c.index,
            "opened": False,
            "frames": 0,
            "size": None,
            "fourcc": None,
            "error": None,
        }
        t = threading.Thread(target=work, args=(c,), name=f"probe-cam{c.id}", daemon=True)
        threads.append(t)
        t.start()

    started = time.monotonic()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=3.0)
    elapsed = time.monotonic() - started

    print("  id  name          index  opened  size       format  fps")
    for cam_id in sorted(results):
        r = results[cam_id]
        size = f"{r['size'][0]}x{r['size'][1]}" if r["size"] else "-"
        fps = f"{r['frames'] / elapsed:4.1f}" if r["opened"] else "-"
        mark = "OK " if r["opened"] else "FAIL"
        print(
            f"  {cam_id:2d}  {r['name']:<13} {r['index']:5d}  {mark:6}  "
            f"{size:<9}  {r['fourcc'] or '-':<6}  {fps}"
        )
        if r["error"]:
            print(f"        -> {r['error']}")

    opened = [r for r in results.values() if r["opened"]]
    failed = [r for r in results.values() if not r["opened"]]
    print()
    print(f"{len(opened)} of {len(results)} cameras opened simultaneously")

    if not failed:
        print("All cameras work together. If the server still fails, the difference is")
        print("not concurrency - look at what else the server does differently.")
        return 0

    formats = {r["fourcc"] for r in opened if r["fourcc"]}
    print("This reproduces the server's failure, so the cause is holding several open")
    print("at once - not the camera itself (--cameras opens them one at a time).")
    if not fourcc and formats & {"YUY2", "YUYV", "RGB3", "BGR3", "I420", "NV12"}:
        print()
        print(f"The working cameras negotiated {sorted(formats)}, which is uncompressed.")
        print("Uncompressed UVC streams reserve USB bandwidth up front, so the last")
        print("camera has none left. Test that by re-running with:")
        print("  python -m tools.probe_hardware --cameras-together --fourcc MJPG")
        print("If that opens all of them, set 'fourcc: MJPG' on the cameras in the config.")
    elif fourcc:
        print()
        print(f"{fourcc} did not help. The next thing to try is physical: move one camera")
        print("to a USB port on a different controller (a port on the other side of the")
        print("machine, or a different root hub - not another socket on the same hub).")
    return 1


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
    group.add_argument("--cameras", action="store_true", help="scan camera indices one at a time")
    group.add_argument(
        "--cameras-together",
        action="store_true",
        help="open every configured camera at once, like the server does",
    )
    group.add_argument(
        "--snapshot",
        action="store_true",
        help="save one photo per camera index so you can tell which is which",
    )
    group.add_argument(
        "--check-sides", action="store_true", help="verify left/right are not swapped in the config"
    )
    group.add_argument(
        "--scan-motors", action="store_true", help="ping motors 1-6 on every arm individually"
    )
    parser.add_argument("--config", default="config/workbench.yaml")
    parser.add_argument(
        "--kind", choices=["follower", "leader"], default="follower", help="which arms to open"
    )
    parser.add_argument("--max-index", type=int, default=8, help="how many camera indices to scan")
    parser.add_argument("--out", default="cam_snapshots", help="where --snapshot writes photos")
    parser.add_argument("--seconds", type=float, default=4.0, help="how long each --check-sides step waits")
    parser.add_argument(
        "--fourcc",
        default=None,
        help="force a pixel format for --cameras-together, e.g. MJPG (default: leave it alone)",
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")

    if args.ports:
        return probe_ports()
    if args.arms:
        return probe_arms(args.config, args.kind)
    if args.check_sides:
        return check_sides(args.config, args.kind, args.seconds)
    if args.scan_motors:
        return scan_motors()
    if args.snapshot:
        return snapshot_cameras(args.max_index, args.out)
    if args.cameras_together:
        return cameras_together(args.config, args.fourcc, max(args.seconds, 2.0))
    return probe_cameras(args.max_index)


if __name__ == "__main__":
    raise SystemExit(main())
