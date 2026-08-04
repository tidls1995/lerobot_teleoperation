"""원격 사용자가 자기 장비를 스스로 점검한다.

집에 있는 사람은 작업대를 볼 수 없고, 옆에서 봐 줄 사람도 없다. "안 움직여요"
라는 연락을 받았을 때 원인이 리더 암인지, 캘리브레이션인지, 회선인지, 작업대
서버인지 **그 사람이 스스로 좁힐 수 있어야** 한다.

**진단은 관찰만 한다.** 토크를 끄지도, 캘리브레이션을 쓰지도 않는다. 상태를 보려고
돌렸는데 상태가 바뀌면 그 진단은 믿을 수 없다 (전례: 진단이 스스로 메인 스레드에
'예방주사'를 놓아 모든 판정을 무효로 만들었다 - hardware-setup 5-2).

lerobot 을 import 하지 않는다. exe 로 묶어야 하기 때문이다.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field

from common.config import ArmConfig
from common.feetech_lite import (
    MOTOR_NAMES,
    FeetechLiteBus,
    MotorCalibration,
    looks_uncalibrated,
)
from common.joints import ARM_SIDES
from common.netutil import recv_exactly
from common.protocol import VIDEO_HEADER_SIZE, VideoHeader
from common.serial_ports import describe_ports, resolve_port_spec

log = logging.getLogger(__name__)

#: 서보 전압은 0.1V 단위로 온다.
_VOLTS_PER_UNIT = 0.1

#: 이보다 낮으면 전원이 의심스럽다. STS3215 는 보통 6~7.4V 로 돈다.
LOW_VOLTAGE = 5.5

#: 이보다 뜨거우면 쉬게 해야 한다.
HIGH_TEMP_C = 55


@dataclass
class MotorReport:
    id: int
    name: str
    answered: bool
    calibration: MotorCalibration | None = None
    torque_on: bool | None = None
    load: int | None = None
    volts: float | None = None
    temp_c: int | None = None

    @property
    def uncalibrated(self) -> bool:
        return self.calibration is not None and looks_uncalibrated(self.calibration)

    @property
    def warnings(self) -> list[str]:
        out = []
        if not self.answered:
            out.append("no answer")
            return out
        if self.uncalibrated:
            out.append("never calibrated")
        if self.torque_on:
            out.append("torque is ON - the joint will feel stiff")
        if self.volts is not None and self.volts < LOW_VOLTAGE:
            out.append(f"low voltage {self.volts:.1f}V")
        if self.temp_c is not None and self.temp_c > HIGH_TEMP_C:
            out.append(f"hot: {self.temp_c} C")
        return out


@dataclass
class ArmReport:
    side: str
    serial_number: str | None = None
    port: str | None = None
    error: str | None = None
    motors: list[MotorReport] = field(default_factory=list)
    read_hz: float | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.motors) and all(m.answered for m in self.motors)

    @property
    def warnings(self) -> list[str]:
        out = []
        for motor in self.motors:
            out.extend(f"{motor.name}: {w}" for w in motor.warnings)
        return out


def diagnose_arm(side: str, arm: ArmConfig, seconds: float = 1.0) -> ArmReport:
    """팔 한 대를 열어 모터 6개를 훑는다. 아무것도 바꾸지 않는다."""
    report = ArmReport(side=side, serial_number=arm.serial_number)

    try:
        report.port = resolve_port_spec(arm.serial_number, arm.port)
    except Exception as exc:
        report.error = f"{exc}"
        return report

    bus = FeetechLiteBus(port=report.port)
    try:
        bus.connect()
    except Exception as exc:
        report.error = f"cannot open {report.port}: {exc}"
        return report

    try:
        for index, name in enumerate(MOTOR_NAMES):
            motor_id = index + 1
            motor = MotorReport(id=motor_id, name=name, answered=bus.ping(motor_id))
            if motor.answered:
                # 하나가 안 읽혀도 나머지는 보여준다. 어디까지 되는지가 단서다.
                try:
                    motor.calibration = MotorCalibration(
                        id=motor_id,
                        homing_offset=bus.read("Homing_Offset", motor_id),
                        range_min=bus.read("Min_Position_Limit", motor_id),
                        range_max=bus.read("Max_Position_Limit", motor_id),
                    )
                    motor.torque_on = bool(bus.read("Torque_Enable", motor_id))
                    motor.load = bus.read("Present_Load", motor_id)
                    motor.volts = bus.read("Present_Voltage", motor_id) * _VOLTS_PER_UNIT
                    motor.temp_c = bus.read("Present_Temperature", motor_id)
                except Exception as exc:
                    log.debug("motor %d: partial read failed: %s", motor_id, exc)
            report.motors.append(motor)

        if all(m.answered for m in report.motors):
            report.read_hz = _measure_read_rate(bus, seconds)
    finally:
        bus.close()

    return report


def _measure_read_rate(bus: FeetechLiteBus, seconds: float) -> float | None:
    """실제로 몇 Hz 로 읽히는가. 조종은 60Hz 를 요구한다."""
    deadline = time.monotonic() + seconds
    started = time.monotonic()
    reads = 0
    try:
        while time.monotonic() < deadline:
            bus.sync_read_positions()
            reads += 1
    except Exception as exc:
        log.debug("read rate measurement stopped early: %s", exc)
    elapsed = time.monotonic() - started
    return reads / elapsed if elapsed > 0 and reads else None


def diagnose_arms(arms: dict[str, ArmConfig], seconds: float = 1.0) -> list[ArmReport]:
    return [diagnose_arm(side, arms[side], seconds) for side in ARM_SIDES if side in arms]


# --- 작업대 카메라 ---------------------------------------------------------------


@dataclass
class CameraReport:
    cam_id: int
    frames: int = 0
    bytes_total: int = 0

    def fps(self, seconds: float) -> float:
        return self.frames / seconds if seconds > 0 else 0.0

    def average_kb(self) -> float:
        return self.bytes_total / self.frames / 1024.0 if self.frames else 0.0


@dataclass
class VideoReport:
    error: str | None = None
    cameras: list[CameraReport] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.cameras)


def survey_video(host: str, port: int, seconds: float = 3.0, timeout: float = 5.0) -> VideoReport:
    """영상 채널에 붙어 **어느 카메라가 오는지** 센다.

    `tools.check_link` 는 첫 프레임 한 장만 본다. 여기서는 몇 초 받아 카메라별로
    세는데, 화면에 칸이 비었을 때 "그 카메라가 안 오는 것"인지 "전부 안 오는 것"인지
    가르기 위해서다.
    """
    report = VideoReport()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        report.error = (
            f"cannot connect to {host}:{port}/tcp: {exc}. "
            "Is the workbench server running? Is TCP 5556 allowed through the firewall?"
        )
        return report

    per_cam: dict[int, CameraReport] = {}
    started = time.monotonic()
    try:
        sock.settimeout(timeout)
        while time.monotonic() - started < seconds:
            header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
            if header_bytes is None:
                break
            header = VideoHeader.unpack(header_bytes)
            if header is None:
                report.error = (
                    "frame header did not parse - the two PCs are running different "
                    "protocol versions"
                )
                break
            payload = recv_exactly(sock, header.length)
            if payload is None:
                break
            entry = per_cam.setdefault(header.cam_id, CameraReport(cam_id=header.cam_id))
            entry.frames += 1
            entry.bytes_total += header.length
    except socket.timeout:
        if not per_cam:
            report.error = (
                f"connected to {host}:{port}/tcp but no frame arrived within {timeout:.1f}s. "
                "The workbench server has no working camera."
            )
    finally:
        sock.close()

    report.seconds = time.monotonic() - started
    report.cameras = [per_cam[k] for k in sorted(per_cam)]
    if not report.cameras and report.error is None:
        report.error = "connected but no frames arrived"
    return report


# --- 사람이 읽는 형태 -------------------------------------------------------------


def format_arm_report(report: ArmReport) -> str:
    lines = [f"{report.side} arm  (serial {report.serial_number or '-'})"]
    if report.error:
        lines.append(f"  NOT FOUND: {report.error}")
        lines.append("")
        lines.append("  Plug the arm in, or check which ports exist:")
        lines.append(describe_ports())
        return "\n".join(lines)

    lines.append(f"  port {report.port}")
    lines.append("")
    lines.append("  motor            id  answers  torque   volts  temp  homing   min    max")
    for motor in report.motors:
        cal = motor.calibration
        answers = "yes" if motor.answered else "NO "
        torque = "-" if motor.torque_on is None else ("ON" if motor.torque_on else "off")
        volts = "-" if motor.volts is None else f"{motor.volts:.1f}"
        temp = "-" if motor.temp_c is None else str(motor.temp_c)
        homing = "-" if cal is None else str(cal.homing_offset)
        low = "-" if cal is None else str(cal.range_min)
        high = "-" if cal is None else str(cal.range_max)
        lines.append(
            f"  {motor.name:<15} {motor.id:>2}  {answers:>7}  {torque:>6}  {volts:>5}  "
            f"{temp:>4}  {homing:>6} {low:>5} {high:>6}"
        )

    if report.read_hz is not None:
        lines.append("")
        note = "" if report.read_hz >= 120 else "   <-- low; teleoperation needs 60 Hz"
        lines.append(f"  read rate {report.read_hz:.0f} Hz{note}")

    warnings = report.warnings
    if warnings:
        lines.append("")
        for warning in warnings:
            lines.append(f"  ! {warning}")
        if any("never calibrated" in w for w in warnings):
            lines.append("")
            lines.append("  A never-calibrated joint reports wrong angles, so the arm at the")
            lines.append("  workbench would move to a wrong pose. Calibrate before teleoperating.")
        if any("torque is ON" in w for w in warnings):
            lines.append("")
            lines.append("  Torque ON just means the arm feels stiff right now. Starting")
            lines.append("  teleoperation turns it off.")
    return "\n".join(lines)


def format_video_report(report: VideoReport, expected: int | None = None) -> str:
    lines = ["workbench cameras"]
    if report.error:
        lines.append(f"  FAILED: {report.error}")
        return "\n".join(lines)

    lines.append("  cam   frames    fps    average size")
    for cam in report.cameras:
        lines.append(
            f"  {cam.cam_id:>3}   {cam.frames:>6}   {cam.fps(report.seconds):>4.1f}   "
            f"{cam.average_kb():>6.1f} KB"
        )
    if expected is not None and len(report.cameras) < expected:
        missing = expected - len(report.cameras)
        lines.append("")
        lines.append(f"  ! {missing} of {expected} cameras sent nothing.")
        lines.append("  The link is fine - the problem is at the workbench, not here.")
    return "\n".join(lines)
