"""YAML 설정 파일을 데이터클래스로 읽어들이고 검증한다.

검증을 여기서 강하게 하는 이유: 관절 한계가 하나라도 빠지면 그 관절에는
안전 클램프가 걸리지 않는다. 조용히 통과시키는 것보다 기동 시 죽는 편이 낫다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from common.joints import ARM_SIDES, GRIPPER_INDICES
from common.protocol import JOINT_NAMES


class ConfigError(Exception):
    """설정 파일이 유효하지 않다."""


@dataclass(frozen=True)
class CameraConfig:
    id: int
    name: str
    index: int
    width: int
    height: int
    fps: int
    jpeg_quality: int


@dataclass(frozen=True)
class ArmConfig:
    """팔 한 대를 어떻게 찾고 어떤 캘리브레이션을 쓸지.

    serial_number 와 port 중 **정확히 하나**만 지정한다. 둘 다 주면 어느 쪽이
    이겼는지 모르는 상태가 되고, 좌우가 뒤바뀐 채 조종을 시작할 수 있다.
    """

    side: str
    serial_number: str | None
    port: str | None
    calibration_id: str


@dataclass(frozen=True)
class SafetyConfig:
    align_threshold_deg: float
    max_step_deg: float
    follow_error_deg: float
    follow_error_hold_ms: int
    watchdog_ms: int
    joint_limits: dict[str, tuple[float, float]]
    #: 그리퍼는 단위가 퍼센트(0~100)라 도 단위 값과 섞을 수 없다 (스펙 §4.3).
    gripper_max_step: float = 4.0
    gripper_limits: tuple[float, float] = (0.0, 100.0)

    #: HOLD 에서 리셋했을 때 팔로워를 되돌릴 자세. 관절 이름 → 값.
    #: None 이면 호밍 없이 그 자리에서 ALIGNING 으로 간다 (구버전 동작).
    #: **리더가 편하게 잡을 수 있는 자세를 리더에서 읽어 적는다** - 팔로워에서
    #: 읽으면 리더가 도달 못 하는 자세를 목표로 삼게 된다.
    home_pose: dict[str, float] | None = None
    #: 호밍 중 프레임당 최대 이동량. 조종(90도/초)보다 느리게 잡는다. 먼 거리를
    #: 움직이고 조종자가 팔을 안 보고 있을 수도 있기 때문이다.
    homing_max_step: float = 0.5  # 60Hz 기준 30도/초
    #: 이만큼 안으로 들어오면 호밍 완료로 보고 ALIGNING 으로 넘어간다.
    homing_tolerance: float = 1.0

    def max_step_for(self, index: int) -> float:
        """이 관절의 프레임당 최대 이동량. 그리퍼만 다른 값을 쓴다."""
        return self.gripper_max_step if index in GRIPPER_INDICES else self.max_step_deg

    def home_pose_list(self) -> list[float] | None:
        """home_pose 를 관절 순서대로 12칸 배열로. 설정이 없으면 None."""
        if self.home_pose is None:
            return None
        return [self.home_pose[name] for name in JOINT_NAMES]


@dataclass(frozen=True)
class WorkbenchConfig:
    use_mock: bool
    control_port: int
    video_port: int
    cameras: list[CameraConfig]
    safety: SafetyConfig
    arms: dict[str, ArmConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class HomeConfig:
    server_host: str
    control_port: int
    video_port: int
    use_mock: bool
    client_watchdog_ms: int
    arms: dict[str, ArmConfig] = field(default_factory=dict)


def _read_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {p}")
    return data


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required key '{key}' in {where}")
    return data[key]


def _check_port(value: Any, key: str) -> int:
    if not isinstance(value, int) or not (1024 <= value <= 65535):
        raise ConfigError(f"{key}: port must be an integer in 1024..65535, got {value!r}")
    return value


def _parse_joint_limits(raw: Any) -> dict[str, tuple[float, float]]:
    if not isinstance(raw, dict):
        raise ConfigError("safety.joint_limits must be a mapping of joint name to [min, max]")

    unknown = set(raw) - set(JOINT_NAMES)
    if unknown:
        raise ConfigError(f"unknown joint name(s) in joint_limits: {sorted(unknown)}")

    missing = set(JOINT_NAMES) - set(raw)
    if missing:
        raise ConfigError(f"joint_limits is missing entries for: {sorted(missing)}")

    limits: dict[str, tuple[float, float]] = {}
    for name in JOINT_NAMES:
        pair = raw[name]
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise ConfigError(f"joint_limits[{name}] must be [min, max], got {pair!r}")
        lo, hi = float(pair[0]), float(pair[1])
        if lo >= hi:
            raise ConfigError(f"joint_limits[{name}]: min ({lo}) must be less than max ({hi})")
        if name.endswith("gripper") and not (0.0 <= lo < hi <= 100.0):
            raise ConfigError(
                f"joint_limits[{name}]: gripper units are percent, so limits must lie "
                f"within [0, 100], got [{lo}, {hi}]"
            )
        limits[name] = (lo, hi)
    return limits


def _parse_safety(raw: Any) -> SafetyConfig:
    if not isinstance(raw, dict):
        raise ConfigError("safety section must be a mapping")
    gripper_limits = raw.get("gripper_limits", [0.0, 100.0])
    if not (isinstance(gripper_limits, (list, tuple)) and len(gripper_limits) == 2):
        raise ConfigError(f"safety.gripper_limits must be [min, max], got {gripper_limits!r}")
    limits = _parse_joint_limits(_require(raw, "joint_limits", "safety"))
    home_pose = _parse_home_pose(raw["home_pose"], limits) if "home_pose" in raw else None
    return SafetyConfig(
        align_threshold_deg=float(_require(raw, "align_threshold_deg", "safety")),
        max_step_deg=float(_require(raw, "max_step_deg", "safety")),
        follow_error_deg=float(_require(raw, "follow_error_deg", "safety")),
        follow_error_hold_ms=int(_require(raw, "follow_error_hold_ms", "safety")),
        watchdog_ms=int(_require(raw, "watchdog_ms", "safety")),
        joint_limits=limits,
        gripper_max_step=float(raw.get("gripper_max_step", 4.0)),
        gripper_limits=(float(gripper_limits[0]), float(gripper_limits[1])),
        home_pose=home_pose,
        homing_max_step=float(raw.get("homing_max_step", 0.5)),
        homing_tolerance=float(raw.get("homing_tolerance", 1.0)),
    )


def _parse_home_pose(raw: Any, limits: dict[str, tuple[float, float]]) -> dict[str, float]:
    """호밍 목표 자세. 관절 12개가 모두 있어야 하고 관절 한계 안이어야 한다.

    한계 밖의 목표를 허용하면 클램프에 걸려 영원히 도착하지 못하고, 호밍이
    끝나지 않는다.
    """
    if not isinstance(raw, dict):
        raise ConfigError("safety.home_pose must be a mapping of joint name to value")

    unknown = set(raw) - set(JOINT_NAMES)
    if unknown:
        raise ConfigError(f"unknown joint name(s) in home_pose: {sorted(unknown)}")
    missing = set(JOINT_NAMES) - set(raw)
    if missing:
        raise ConfigError(f"home_pose is missing entries for: {sorted(missing)}")

    pose: dict[str, float] = {}
    for name in JOINT_NAMES:
        value = float(raw[name])
        lo, hi = limits[name]
        if not (lo <= value <= hi):
            raise ConfigError(
                f"home_pose[{name}] = {value} is outside joint_limits [{lo}, {hi}]; "
                "the arm would never reach it and homing would never finish"
            )
        pose[name] = value
    return pose


def _parse_arms(raw: Any) -> dict[str, ArmConfig]:
    if not isinstance(raw, dict):
        raise ConfigError("arms must be a mapping of side to arm settings")

    unknown = set(raw) - set(ARM_SIDES)
    if unknown:
        raise ConfigError(f"unknown arm side(s): {sorted(unknown)}, expected {list(ARM_SIDES)}")
    missing = set(ARM_SIDES) - set(raw)
    if missing:
        raise ConfigError(f"arms is missing side(s): {sorted(missing)}")

    arms: dict[str, ArmConfig] = {}
    for side in ARM_SIDES:
        entry = raw[side]
        if not isinstance(entry, dict):
            raise ConfigError(f"arms.{side} must be a mapping, got {entry!r}")
        serial_number = entry.get("serial_number")
        port = entry.get("port")
        if (serial_number is None) == (port is None):
            raise ConfigError(
                f"arms.{side}: specify exactly one of 'serial_number' or 'port' "
                "(serial_number is preferred; COM numbers move around)"
            )
        arms[side] = ArmConfig(
            side=side,
            serial_number=str(serial_number) if serial_number is not None else None,
            port=str(port) if port is not None else None,
            calibration_id=str(_require(entry, "calibration_id", f"arms.{side}")),
        )
    return arms


def _parse_cameras(raw: Any) -> list[CameraConfig]:
    # 빈 목록을 허용한다. USB 포트가 부족해 카메라 없이 팔만 검증하는 구성이
    # 있고(2단계-A), 카메라 0대는 오류가 아니라 선택이다.
    if not isinstance(raw, list):
        raise ConfigError(f"cameras must be a list, got {type(raw).__name__}")
    cams: list[CameraConfig] = []
    seen: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"camera entry must be a mapping, got {entry!r}")
        cam = CameraConfig(
            id=int(_require(entry, "id", "camera")),
            name=str(_require(entry, "name", "camera")),
            index=int(_require(entry, "index", "camera")),
            width=int(_require(entry, "width", "camera")),
            height=int(_require(entry, "height", "camera")),
            fps=int(_require(entry, "fps", "camera")),
            jpeg_quality=int(_require(entry, "jpeg_quality", "camera")),
        )
        if cam.id in seen:
            raise ConfigError(f"duplicate camera id: {cam.id}")
        if not (0 <= cam.jpeg_quality <= 100):
            raise ConfigError(f"camera {cam.id}: jpeg_quality must be 0..100")
        if cam.fps <= 0:
            raise ConfigError(f"camera {cam.id}: fps must be positive")
        seen.add(cam.id)
        cams.append(cam)
    return cams


def load_workbench_config(path: str | Path) -> WorkbenchConfig:
    data = _read_yaml(path)
    return WorkbenchConfig(
        use_mock=bool(_require(data, "use_mock", "workbench config")),
        control_port=_check_port(_require(data, "control_port", "workbench config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "workbench config"), "video_port"),
        cameras=_parse_cameras(_require(data, "cameras", "workbench config")),
        safety=_parse_safety(_require(data, "safety", "workbench config")),
        arms=_parse_arms(data["arms"]) if "arms" in data else {},
    )


def load_home_config(path: str | Path) -> HomeConfig:
    data = _read_yaml(path)
    return HomeConfig(
        server_host=str(_require(data, "server_host", "home config")),
        control_port=_check_port(_require(data, "control_port", "home config"), "control_port"),
        video_port=_check_port(_require(data, "video_port", "home config"), "video_port"),
        use_mock=bool(_require(data, "use_mock", "home config")),
        client_watchdog_ms=int(_require(data, "client_watchdog_ms", "home config")),
        arms=_parse_arms(data["arms"]) if "arms" in data else {},
    )
