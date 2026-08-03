import json

import pytest

from common.joints import MOTOR_NAMES
from tools.move_calibration import (
    CalibrationError,
    export_calibration,
    import_calibration,
    verify_calibration,
)


def good_calibration():
    return {
        name: {"id": i + 1, "drive_mode": 0, "homing_offset": 10, "range_min": 0, "range_max": 4095}
        for i, name in enumerate(MOTOR_NAMES)
    }


def make_root(tmp_path, ids_by_kind):
    """ids_by_kind: {"robots/so_follower": ["follower_left"], ...}"""
    root = tmp_path / "calibration"
    for subdir, ids in ids_by_kind.items():
        d = root / subdir
        d.mkdir(parents=True, exist_ok=True)
        for cid in ids:
            (d / f"{cid}.json").write_text(json.dumps(good_calibration()), encoding="utf-8")
    return root


def test_export_copies_the_requested_ids(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left", "follower_right"]})
    dest = tmp_path / "out"
    copied = export_calibration(["follower_left", "follower_right"], dest, root=root)
    assert len(copied) == 2
    assert (dest / "robots" / "so_follower" / "follower_left.json").is_file()


def test_export_keeps_the_subdirectory_layout(tmp_path):
    """robots/ 와 teleoperators/ 가 섞이면 안 된다. lerobot 이 경로로 찾는다."""
    root = make_root(
        tmp_path,
        {"robots/so_follower": ["follower_left"], "teleoperators/so_leader": ["leader_left"]},
    )
    dest = tmp_path / "out"
    export_calibration(["follower_left", "leader_left"], dest, root=root)
    assert (dest / "robots" / "so_follower" / "follower_left.json").is_file()
    assert (dest / "teleoperators" / "so_leader" / "leader_left.json").is_file()


def test_export_rejects_an_unknown_id(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    with pytest.raises(CalibrationError, match="nope"):
        export_calibration(["nope"], tmp_path / "out", root=root)


def test_import_puts_files_back_in_the_same_layout(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    dest = tmp_path / "out"
    export_calibration(["follower_left"], dest, root=root)

    new_root = tmp_path / "other_pc"
    imported = import_calibration(dest, root=new_root)
    assert len(imported) == 1
    assert (new_root / "robots" / "so_follower" / "follower_left.json").is_file()


def test_import_refuses_to_silently_overwrite_a_different_file(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    dest = tmp_path / "out"
    export_calibration(["follower_left"], dest, root=root)

    other = make_root(tmp_path / "b", {"robots/so_follower": ["follower_left"]})
    target = other / "robots" / "so_follower" / "follower_left.json"
    target.write_text(json.dumps({"different": True}), encoding="utf-8")

    import_calibration(dest, root=other)
    backups = list((other / "robots" / "so_follower").glob("*.bak"))
    assert backups, "덮어쓰기 전에 백업을 남겨야 한다"


def test_verify_passes_for_complete_files(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    assert verify_calibration(["follower_left"], root=root) == []


def test_verify_reports_a_missing_id(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    problems = verify_calibration(["follower_left", "follower_right"], root=root)
    assert len(problems) == 1
    assert "follower_right" in problems[0]


def test_verify_reports_a_file_missing_motors(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    path = root / "robots" / "so_follower" / "follower_left.json"
    partial = good_calibration()
    del partial["gripper"]
    path.write_text(json.dumps(partial), encoding="utf-8")
    problems = verify_calibration(["follower_left"], root=root)
    assert len(problems) == 1
    assert "gripper" in problems[0]


def test_verify_reports_unparseable_json(tmp_path):
    root = make_root(tmp_path, {"robots/so_follower": ["follower_left"]})
    (root / "robots" / "so_follower" / "follower_left.json").write_text("{ broken", encoding="utf-8")
    problems = verify_calibration(["follower_left"], root=root)
    assert len(problems) == 1
