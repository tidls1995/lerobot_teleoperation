"""exe 진입점. 원격 사용자가 보는 유일한 화면이므로 실패 경로가 특히 중요하다.

집에 있는 사람은 창이 닫혀버리면 아무것도 전할 수 없다. "무엇이 잘못됐는지 화면에
남는가"를 여기서 검증한다.
"""

import pytest

from common.config import ConfigError, load_home_config
from home.launcher import CONFIG_TEMPLATE, find_config

GOOD = """\
server_host: "192.168.0.3"
control_port: 5555
video_port: 5556
use_mock: false
client_watchdog_ms: 300
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- 설정 파일 찾기 ---------------------------------------------------------------


def test_an_explicit_path_wins(tmp_path):
    path = write(tmp_path, "mine.yaml", GOOD)
    assert find_config(str(path)) == path


def test_it_finds_the_settings_file_next_to_the_exe(tmp_path, monkeypatch):
    import home.launcher as mod

    path = write(tmp_path, "home.yaml", GOOD)
    monkeypatch.setattr(mod, "_app_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    assert find_config() == path


def test_it_also_finds_the_repository_layout(tmp_path, monkeypatch):
    """개발 PC 에서는 config/home.yaml 에 있다. 같은 exe 가 양쪽에서 돌아야 한다."""
    import home.launcher as mod

    path = write(tmp_path, "config/home.yaml", GOOD)
    monkeypatch.setattr(mod, "_app_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    assert find_config() == path


def test_a_missing_settings_file_leaves_a_template_behind(tmp_path, monkeypatch):
    """"설정 파일이 없습니다"만 띄우면 원격 사용자는 무엇을 써야 할지 모른다.

    본보기를 만들어 두면 연락 한 번이 줄어든다.
    """
    import home.launcher as mod

    monkeypatch.setattr(mod, "_app_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as exc:
        find_config()

    created = tmp_path / "home.yaml"
    assert created.is_file()
    assert str(created) in str(exc.value)
    assert "Notepad" in str(exc.value), "무엇을 하라는 것인지 적혀 있어야 한다"


def test_the_template_is_valid_yaml_once_the_blanks_are_filled(tmp_path):
    """본보기가 그 자체로 깨져 있으면 채워 넣어도 안 열린다."""
    filled = CONFIG_TEMPLATE.replace("CHANGE-ME", "FILLED")
    path = write(tmp_path, "home.yaml", filled)
    cfg = load_home_config(path)
    assert cfg.server_host == "FILLED"
    assert set(cfg.arms) == {"left", "right"}
    assert cfg.cameras == 2
    assert cfg.clutch == "toggle"


def test_the_template_does_not_ship_a_real_address():
    """공개 저장소이고, 작업대 주소는 곧 로봇의 열쇠다 (인증이 없다)."""
    assert "CHANGE-ME" in CONFIG_TEMPLATE
    for line in CONFIG_TEMPLATE.splitlines():
        if line.startswith("server_host:"):
            assert "CHANGE-ME" in line


# --- exe 로 묶었을 때 설정을 어디서 찾는가 ---------------------------------------


def test_frozen_looks_next_to_the_exe_not_the_unpacked_code(tmp_path, monkeypatch):
    """PyInstaller 는 코드를 임시 폴더에 푼다. 거기서 찾으면 영영 못 찾는다."""
    import home.launcher as mod

    monkeypatch.setattr(mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mod.sys, "executable", str(tmp_path / "teleop.exe"), raising=False)
    assert mod._app_dir() == tmp_path


def test_not_frozen_uses_the_working_directory(tmp_path, monkeypatch):
    import home.launcher as mod

    monkeypatch.delattr(mod.sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    assert mod._app_dir() == tmp_path


# --- 설정 항목 -------------------------------------------------------------------


def test_camera_count_and_clutch_have_defaults(tmp_path):
    """exe 는 명령줄 인자를 받지 않으므로 설정에 없으면 기본값이 있어야 한다."""
    cfg = load_home_config(write(tmp_path, "h.yaml", GOOD))
    assert cfg.cameras == 2
    assert cfg.clutch == "toggle"


def test_a_bad_clutch_mode_is_refused_with_the_allowed_values(tmp_path):
    path = write(tmp_path, "h.yaml", GOOD + "clutch: pedal\n")
    with pytest.raises(ConfigError, match="hold.*toggle"):
        load_home_config(path)


def test_a_negative_camera_count_is_refused(tmp_path):
    path = write(tmp_path, "h.yaml", GOOD + "cameras: -1\n")
    with pytest.raises(ConfigError, match="cameras"):
        load_home_config(path)
