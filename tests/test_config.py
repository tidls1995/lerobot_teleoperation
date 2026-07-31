import pytest

from common.config import ConfigError, load_home_config, load_workbench_config
from common.protocol import JOINT_NAMES

# YAML 은 들여쓰기가 곧 구조다. textwrap.dedent 를 쓰면 삽입된 블록과 본문의
# 들여쓰기가 서로 다르게 깎여 계층이 무너지므로, 여기서는 그대로 적는다.
LIMIT_INDENT = "    "
FULL_LIMITS = "\n".join(f"{LIMIT_INDENT}{name}: [-120.0, 120.0]" for name in JOINT_NAMES)

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
    return f"{LIMIT_INDENT}{joint_name}: [-120.0, 120.0]"


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
    assert cfg.safety.joint_limits["left_gripper"] == (-120.0, 120.0)


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
