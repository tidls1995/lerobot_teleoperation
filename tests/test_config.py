import pytest

from common.config import ConfigError, load_home_config, load_workbench_config
from common.protocol import JOINT_NAMES

# YAML 은 들여쓰기가 곧 구조다. textwrap.dedent 를 쓰면 삽입된 블록과 본문의
# 들여쓰기가 서로 다르게 깎여 계층이 무너지므로, 여기서는 그대로 적는다.
LIMIT_INDENT = "    "


def limit_range(joint_name: str) -> str:
    """그리퍼는 퍼센트(0~100), 나머지는 도(degree) 단위다 (스펙 §4.3)."""
    return "[0.0, 100.0]" if joint_name.endswith("gripper") else "[-120.0, 120.0]"


FULL_LIMITS = "\n".join(f"{LIMIT_INDENT}{name}: {limit_range(name)}" for name in JOINT_NAMES)

WORKBENCH_YAML = f"""use_mock: true
control_port: 5555
video_port: 5556
cameras:
  - {{ id: 0, name: front,       index: 0, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
  - {{ id: 1, name: wrist_left,  index: 1, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
  - {{ id: 2, name: wrist_right, index: 2, width: 320, height: 240, fps: 15, jpeg_quality: 80 }}
safety:
  align_threshold_deg: 3.0
  max_step_deg: 1.5
  follow_error_deg: 15.0
  follow_error_hold_ms: 500
  watchdog_ms: 200
  joint_limits:
{FULL_LIMITS}
"""

HOME_YAML = """server_host: "127.0.0.1"
control_port: 5555
video_port: 5556
use_mock: true
client_watchdog_ms: 300
"""


def limit_line(joint_name: str) -> str:
    return f"{LIMIT_INDENT}{joint_name}: {limit_range(joint_name)}"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_workbench_config(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", WORKBENCH_YAML))
    assert cfg.use_mock is True
    assert cfg.control_port == 5555
    assert cfg.video_port == 5556
    assert len(cfg.cameras) == 3
    assert cfg.cameras[1].name == "wrist_left"
    assert cfg.cameras[0].width == 320
    assert cfg.safety.max_step_deg == 1.5
    assert cfg.safety.watchdog_ms == 200
    # 그리퍼는 퍼센트 단위이므로 다른 관절과 범위가 다르다
    assert cfg.safety.joint_limits["left_gripper"] == (0.0, 100.0)
    assert cfg.safety.joint_limits["left_elbow_flex"] == (-120.0, 120.0)


def test_load_home_config(tmp_path):
    cfg = load_home_config(_write(tmp_path, "h.yaml", HOME_YAML))
    assert cfg.server_host == "127.0.0.1"
    assert cfg.client_watchdog_ms == 300
    assert cfg.use_mock is True


def test_missing_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(limit_line(JOINT_NAMES[3]) + "\n", "")
    with pytest.raises(ConfigError, match=JOINT_NAMES[3]):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_unknown_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(
        limit_line(JOINT_NAMES[0]), f"{LIMIT_INDENT}not_a_joint: [-120.0, 120.0]"
    )
    with pytest.raises(ConfigError, match="not_a_joint"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_inverted_joint_limit_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace(
        limit_line(JOINT_NAMES[0]), f"{LIMIT_INDENT}{JOINT_NAMES[0]}: [50.0, 10.0]"
    )
    with pytest.raises(ConfigError, match="min .* max"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_duplicate_camera_id_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace("{ id: 1, name: wrist_left", "{ id: 0, name: wrist_left")
    with pytest.raises(ConfigError, match="camera id"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_bad_port_is_rejected(tmp_path):
    broken = WORKBENCH_YAML.replace("control_port: 5555", "control_port: 99999")
    with pytest.raises(ConfigError, match="port"):
        load_workbench_config(_write(tmp_path, "w.yaml", broken))


def test_missing_required_key_is_rejected(tmp_path):
    broken = HOME_YAML.replace('server_host: "127.0.0.1"\n', "")
    with pytest.raises(ConfigError, match="server_host"):
        load_home_config(_write(tmp_path, "h.yaml", broken))


def test_shipped_config_files_load():
    """리포지토리에 들어 있는 실제 설정 파일이 유효해야 한다."""
    w = load_workbench_config("config/workbench.yaml")
    h = load_home_config("config/home.yaml")
    assert len(w.cameras) == 3
    assert w.control_port == h.control_port
    assert w.video_port == h.video_port


# --- 2단계: arms 섹션과 그리퍼 전용 안전값 --------------------------------

ARMS_YAML = """arms:
  left:  { serial_number: "AB12CD34", calibration_id: "left" }
  right: { serial_number: "EF56GH78", calibration_id: "right" }
"""

GRIPPER_YAML = """  gripper_max_step: 4.0
  gripper_limits: [0.0, 100.0]
"""


def workbench_with_arms():
    """1단계 YAML 에 arms 섹션과 그리퍼 안전값을 얹는다."""
    text = WORKBENCH_YAML.replace("  joint_limits:", GRIPPER_YAML + "  joint_limits:")
    # 그리퍼 한계는 퍼센트 단위이므로 도 단위 기본값을 덮어쓴다
    for name in ("left_gripper", "right_gripper"):
        text = text.replace(limit_line(name), f"{LIMIT_INDENT}{name}: [0.0, 100.0]")
    return text + ARMS_YAML


def home_with_arms():
    return HOME_YAML + ARMS_YAML


def test_workbench_config_reads_arms(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    assert set(cfg.arms) == {"left", "right"}
    assert cfg.arms["left"].serial_number == "AB12CD34"
    assert cfg.arms["left"].port is None
    assert cfg.arms["right"].calibration_id == "right"


def test_home_config_reads_arms(tmp_path):
    cfg = load_home_config(_write(tmp_path, "h.yaml", home_with_arms()))
    assert set(cfg.arms) == {"left", "right"}
    assert cfg.arms["right"].serial_number == "EF56GH78"


def test_arm_may_specify_port_instead_of_serial(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ port: "COM3", calibration_id: "left" }',
    )
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", text))
    assert cfg.arms["left"].port == "COM3"
    assert cfg.arms["left"].serial_number is None


def test_arm_with_both_port_and_serial_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ serial_number: "AB12CD34", port: "COM3", calibration_id: "left" }',
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_arm_with_neither_port_nor_serial_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '{ serial_number: "AB12CD34", calibration_id: "left" }',
        '{ calibration_id: "left" }',
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_missing_arm_side_is_rejected(tmp_path):
    text = workbench_with_arms().replace(
        '  right: { serial_number: "EF56GH78", calibration_id: "right" }\n', ""
    )
    with pytest.raises(ConfigError, match="right"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_gripper_safety_values_are_read(tmp_path):
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    assert cfg.safety.gripper_max_step == 4.0
    assert cfg.safety.gripper_limits == (0.0, 100.0)


def test_max_step_for_uses_gripper_value_at_gripper_indices(tmp_path):
    from common.joints import GRIPPER_INDICES

    cfg = load_workbench_config(_write(tmp_path, "w.yaml", workbench_with_arms()))
    for idx in GRIPPER_INDICES:
        assert cfg.safety.max_step_for(idx) == 4.0
    for idx in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10):
        assert cfg.safety.max_step_for(idx) == cfg.safety.max_step_deg


def test_gripper_joint_limits_outside_percent_range_are_rejected(tmp_path):
    text = workbench_with_arms().replace(
        f"{LIMIT_INDENT}left_gripper: [0.0, 100.0]", f"{LIMIT_INDENT}left_gripper: [-30.0, 100.0]"
    )
    with pytest.raises(ConfigError, match="percent"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))


def test_shipped_config_files_still_load_after_stage2():
    w = load_workbench_config("config/workbench.yaml")
    h = load_home_config("config/home.yaml")
    assert set(w.arms) == {"left", "right"}
    assert set(h.arms) == {"left", "right"}
    assert w.safety.gripper_max_step > 0


def test_camera_list_may_be_empty(tmp_path):
    """2단계-A: USB 포트가 부족해 카메라 없이 팔만 돌린다."""
    text = workbench_with_arms()
    start = text.index("cameras:")
    end = text.index("safety:")
    text = text[:start] + "cameras: []\n" + text[end:]
    cfg = load_workbench_config(_write(tmp_path, "w.yaml", text))
    assert cfg.cameras == []


def test_camera_list_must_still_be_a_list(tmp_path):
    text = workbench_with_arms()
    start = text.index("cameras:")
    end = text.index("safety:")
    text = text[:start] + "cameras: 3\n" + text[end:]
    with pytest.raises(ConfigError, match="list"):
        load_workbench_config(_write(tmp_path, "w.yaml", text))
