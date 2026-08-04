"""lerobot 없는 모터 읽기가 lerobot 과 **같은 숫자**를 내는지 대조한다.

서버(팔로워)는 계속 lerobot 을 쓴다. 클라이언트 계산이 조금이라도 다르면 리더와
팔로워가 서로 다른 각도를 말하게 되고, 정렬이 영영 안 끝나거나 더 나쁘게는 어긋난
채로 조종이 시작된다. 그래서 손으로 기댓값을 적지 않고 **lerobot 을 직접 불러
비교한다** - 내가 공식을 잘못 옮겼다면 손으로 적은 기댓값도 똑같이 틀렸을 것이다.

이 대조는 개발 PC 에서만 돌린다. exe 안에는 lerobot 이 들어가지 않으므로 배포
대상에서는 이 파일이 실행될 일이 없다.
"""

import pytest

from common.feetech_lite import (
    RESOLUTION,
    MotorCalibration,
    decode_sign_magnitude,
    degrees_to_raw,
    encode_sign_magnitude,
    looks_uncalibrated,
    percent_to_raw,
    raw_to_degrees,
    raw_to_percent,
)

#: 실제 팔에서 뜬 값 (follower_left.json, 2026-07-31 캘리브레이션).
ELBOW = MotorCalibration(id=3, homing_offset=986, range_min=933, range_max=3147)
WRIST_ROLL = MotorCalibration(id=5, homing_offset=1468, range_min=0, range_max=4095)
GRIPPER = MotorCalibration(id=6, homing_offset=1694, range_min=2027, range_max=3516)
SHOULDER = MotorCalibration(id=1, homing_offset=-1651, range_min=768, range_max=3256)


# --- 부호-크기 인코딩 ------------------------------------------------------------
#
# 이것을 빠뜨리면 조용히 틀린다. 음수 위치에서 32,000 같은 값이 나오고 그 값이 그대로
# 팔로워 목표각이 된다.


def test_a_positive_value_round_trips():
    assert decode_sign_magnitude(encode_sign_magnitude(1234, 15), 15) == 1234


def test_a_negative_value_round_trips():
    assert decode_sign_magnitude(encode_sign_magnitude(-1234, 15), 15) == -1234


def test_the_sign_bit_is_what_makes_it_negative():
    assert decode_sign_magnitude(0b1000_0000_0000_0001, 15) == -1
    assert decode_sign_magnitude(0b0000_0000_0000_0001, 15) == 1


def test_homing_offset_uses_a_different_sign_bit_than_position():
    """Homing_Offset 은 11번, Present_Position 은 15번이다. 섞으면 안 된다."""
    assert decode_sign_magnitude(0b0000_1000_0000_0001, 11) == -1
    assert decode_sign_magnitude(0b0000_1000_0000_0001, 15) == 2049


def test_a_magnitude_that_does_not_fit_is_refused():
    with pytest.raises(ValueError, match="exceeds"):
        encode_sign_magnitude(40000, 15)


def test_it_matches_lerobots_own_helpers():
    lerobot_utils = pytest.importorskip("lerobot.motors.encoding_utils")
    for value in (-4095, -1, 0, 1, 2048, 32767):
        assert encode_sign_magnitude(value, 15) == lerobot_utils.encode_sign_magnitude(value, 15)
    for encoded in (0, 1, 2048, 32768, 40000, 65535):
        assert decode_sign_magnitude(encoded, 15) == lerobot_utils.decode_sign_magnitude(encoded, 15)


# --- lerobot 과의 대조 ------------------------------------------------------------


def lerobot_normalize(cal: MotorCalibration, norm_mode_name: str, raws: list[int]) -> list[float]:
    """같은 값을 lerobot 에게 물어본다. 포트는 열지 않는다."""
    motors_mod = pytest.importorskip("lerobot.motors")
    feetech_mod = pytest.importorskip("lerobot.motors.feetech")
    from lerobot.motors.motors_bus import MotorCalibration as LerobotCalibration

    norm_mode = getattr(motors_mod.MotorNormMode, norm_mode_name)
    motors = {"m": motors_mod.Motor(cal.id, "sts3215", norm_mode)}
    calibration = {
        "m": LerobotCalibration(
            id=cal.id,
            drive_mode=0,
            homing_offset=cal.homing_offset,
            range_min=cal.range_min,
            range_max=cal.range_max,
        )
    }
    bus = feetech_mod.FeetechMotorsBus(port="COM_NOT_OPENED", motors=motors, calibration=calibration)
    return [bus._normalize({cal.id: raw})[cal.id] for raw in raws]


ALL_CALIBRATIONS = [ELBOW, WRIST_ROLL, GRIPPER, SHOULDER]
SWEEP = list(range(0, RESOLUTION, 37))  # 4096 을 111 개로 훑는다


@pytest.mark.parametrize("cal", ALL_CALIBRATIONS, ids=lambda c: f"motor{c.id}")
def test_degrees_match_lerobot_across_the_whole_range(cal):
    expected = lerobot_normalize(cal, "DEGREES", SWEEP)
    got = [raw_to_degrees(raw, cal) for raw in SWEEP]
    for raw, e, g in zip(SWEEP, expected, got):
        assert g == pytest.approx(e, abs=1e-9), f"motor {cal.id} raw={raw}"


@pytest.mark.parametrize("cal", ALL_CALIBRATIONS, ids=lambda c: f"motor{c.id}")
def test_percent_matches_lerobot_across_the_whole_range(cal):
    expected = lerobot_normalize(cal, "RANGE_0_100", SWEEP)
    got = [raw_to_percent(raw, cal) for raw in SWEEP]
    for raw, e, g in zip(SWEEP, expected, got):
        assert g == pytest.approx(e, abs=1e-9), f"motor {cal.id} raw={raw}"


def test_degrees_match_lerobot_outside_the_calibrated_range():
    """캘리브레이션 범위 밖의 값도 같아야 한다.

    기록 범위는 기계적 한계가 아니라 그때 훑은 만큼이라, 실제로 그 밖의 값이 읽힌다
    (실측: right_elbow_flex 가 기록 ±96.7 을 넘어 97.1 로 읽혔다). lerobot 은 각도
    변환에서 값을 자르지 않으므로 우리도 자르면 안 된다.
    """
    outside = [ELBOW.range_min - 300, ELBOW.range_max + 300]
    expected = lerobot_normalize(ELBOW, "DEGREES", outside)
    got = [raw_to_degrees(raw, ELBOW) for raw in outside]
    assert got == pytest.approx(expected, abs=1e-9)
    assert abs(got[0]) > 0.0 and abs(got[1]) > 0.0


def test_percent_clamps_the_way_lerobot_does():
    """각도와 달리 퍼센트는 자른다. 헷갈리지만 팔로워와 맞추는 것이 우선이다."""
    outside = [GRIPPER.range_min - 500, GRIPPER.range_max + 500]
    expected = lerobot_normalize(GRIPPER, "RANGE_0_100", outside)
    got = [raw_to_percent(raw, GRIPPER) for raw in outside]
    assert got == pytest.approx(expected, abs=1e-9)
    assert got == [0.0, 100.0]


# --- 되돌리기 -------------------------------------------------------------------


@pytest.mark.parametrize("cal", ALL_CALIBRATIONS, ids=lambda c: f"motor{c.id}")
def test_degrees_round_trip_back_to_the_same_tick(cal):
    for raw in SWEEP:
        assert degrees_to_raw(raw_to_degrees(raw, cal), cal) == pytest.approx(raw, abs=1)


def test_percent_round_trips_inside_the_range():
    for raw in range(GRIPPER.range_min, GRIPPER.range_max, 29):
        assert percent_to_raw(raw_to_percent(raw, GRIPPER), GRIPPER) == pytest.approx(raw, abs=1)


# --- 쓸 수 없는 캘리브레이션은 조용히 넘어가지 않는다 -----------------------------


def test_an_empty_range_is_refused_instead_of_dividing_by_zero():
    broken = MotorCalibration(id=1, homing_offset=0, range_min=2000, range_max=2000)
    with pytest.raises(ValueError, match="range_min and range_max are equal"):
        raw_to_percent(2000, broken)
    with pytest.raises(ValueError, match="range_min and range_max are equal"):
        percent_to_raw(50.0, broken)


def test_a_never_calibrated_motor_is_recognised():
    """캘리브레이션을 안 한 모터는 범위가 0~4095 이고 영점도 0 이다."""
    factory = MotorCalibration(id=1, homing_offset=0, range_min=0, range_max=RESOLUTION - 1)
    assert looks_uncalibrated(factory) is True
    assert looks_uncalibrated(ELBOW) is False


def test_wrist_roll_full_turn_is_not_mistaken_for_uncalibrated():
    """wrist_roll 은 한 바퀴를 도는 관절이라 제대로 잡아도 0~4095 가 나온다.

    범위만 보고 판정하면 정상인 팔마다 경고가 뜨고, 사람이 경고를 무시하게 되어
    진짜 미캘리브레이션도 놓친다. 실제 값(homing_offset 1468)으로 확인한다.
    """
    assert WRIST_ROLL.range_min == 0 and WRIST_ROLL.range_max == RESOLUTION - 1
    assert looks_uncalibrated(WRIST_ROLL) is False
