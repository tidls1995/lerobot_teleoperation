import pytest

from common.joints import (
    ARM_SIDES,
    GRIPPER_INDICES,
    MOTOR_NAMES,
    JointNameError,
    arm_slice,
    to_arrays,
    to_dicts,
)
from common.protocol import JOINT_NAMES, N_JOINTS


def left_dict(base=0.0):
    return {f"{m}.pos": base + i for i, m in enumerate(MOTOR_NAMES)}


def right_dict(base=100.0):
    return {f"{m}.pos": base + i for i, m in enumerate(MOTOR_NAMES)}


def test_motor_names_match_joint_names_suffixes():
    """lerobot 모터 이름이 우리 JOINT_NAMES 의 접미사와 정확히 같아야 한다."""
    assert len(MOTOR_NAMES) == 6
    for i, side in enumerate(ARM_SIDES):
        for j, motor in enumerate(MOTOR_NAMES):
            assert JOINT_NAMES[i * 6 + j] == f"{side}_{motor}"


def test_gripper_indices_point_at_grippers():
    for idx in GRIPPER_INDICES:
        assert JOINT_NAMES[idx].endswith("gripper")
    assert GRIPPER_INDICES == (5, 11)


def test_arm_slice_covers_six_joints_each():
    assert arm_slice("left") == slice(0, 6)
    assert arm_slice("right") == slice(6, 12)


def test_arm_slice_rejects_unknown_side():
    with pytest.raises(JointNameError, match="middle"):
        arm_slice("middle")


def test_to_arrays_places_left_then_right():
    arr = to_arrays(left_dict(), right_dict())
    assert len(arr) == N_JOINTS
    assert arr[:6] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    assert arr[6:] == pytest.approx([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])


def test_to_arrays_accepts_keys_without_pos_suffix():
    left = {m: float(i) for i, m in enumerate(MOTOR_NAMES)}
    arr = to_arrays(left, right_dict())
    assert arr[:6] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])


def test_to_arrays_rejects_missing_motor():
    broken = left_dict()
    del broken["elbow_flex.pos"]
    with pytest.raises(JointNameError, match="elbow_flex"):
        to_arrays(broken, right_dict())


def test_to_arrays_rejects_unexpected_motor():
    broken = left_dict()
    broken["thumb.pos"] = 1.0
    with pytest.raises(JointNameError, match="thumb"):
        to_arrays(broken, right_dict())


def test_to_dicts_roundtrips_with_to_arrays():
    original = to_arrays(left_dict(), right_dict())
    left, right = to_dicts(original)
    assert to_arrays(left, right) == pytest.approx(original)


def test_to_dicts_uses_pos_suffix():
    left, right = to_dicts([0.0] * N_JOINTS)
    assert set(left) == {f"{m}.pos" for m in MOTOR_NAMES}
    assert set(right) == {f"{m}.pos" for m in MOTOR_NAMES}


def test_to_dicts_rejects_wrong_length():
    with pytest.raises(ValueError):
        to_dicts([0.0] * 5)


def test_require_both_sides_accepts_left_and_right():
    from common.joints import require_both_sides

    require_both_sides({"left": object(), "right": object()})


def test_require_both_sides_reports_the_missing_one():
    from common.joints import require_both_sides

    with pytest.raises(ValueError, match="right"):
        require_both_sides({"left": object()})
    with pytest.raises(ValueError, match="left"):
        require_both_sides({"right": object()})
