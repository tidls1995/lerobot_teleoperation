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


# --- SDK 규약을 고정한다 ---------------------------------------------------------
#
# 실측(2026-08-04)에서 GroupSyncRead.isAvailable 을 dynamixel SDK 처럼 튜플로 착각해
# TypeError 로 죽었다. 남의 라이브러리 반환값을 확인 없이 가정한 것이다. 아래 가짜들은
# scservo_sdk 소스에서 확인한 실제 규약을 그대로 흉내내므로, 다시 어긋나면 테스트가
# 먼저 깨진다.
#
#   GroupSyncRead.isAvailable(id, addr, len) -> bool          (튜플이 아니다)
#   GroupSyncRead.getData(id, addr, len)     -> int
#   GroupSyncRead.txRxPacket()               -> comm result
#   PacketHandler.readNByteTxRx(port, id, addr) -> (value, result, error)

from common.feetech_lite import ADDR, SIGN_BIT, FeetechLiteBus, MotorError

COMM_SUCCESS = 0


class FakeSyncReader:
    def __init__(self, raw_by_id, fail_times=0, missing=()):
        self._raw = raw_by_id
        self._fail_times = fail_times
        self._missing = set(missing)
        self.tx_calls = 0

    def addParam(self, scs_id):
        return True

    def txRxPacket(self):
        self.tx_calls += 1
        if self.tx_calls <= self._fail_times:
            return 1  # COMM_PORT_BUSY 등, 성공이 아닌 값
        return COMM_SUCCESS

    def isAvailable(self, scs_id, address, data_length):
        return scs_id not in self._missing

    def getData(self, scs_id, address, data_length):
        return self._raw[scs_id]


class FakePacketHandler:
    def __init__(self, values=None, fail_times=0):
        self._values = values or {}
        self._fail_times = fail_times
        self.calls = 0

    def _read(self, port, scs_id, address):
        self.calls += 1
        if self.calls <= self._fail_times:
            return 0, 1, 0
        return self._values.get((scs_id, address), 0), COMM_SUCCESS, 0

    read1ByteTxRx = _read
    read2ByteTxRx = _read

    def ping(self, port, scs_id):
        self.calls += 1
        if self.calls <= self._fail_times:
            return 0, 1, 0
        return 777, COMM_SUCCESS, 0

    def getTxRxResult(self, result):
        return f"result {result}"


def wire(bus, sync_reader=None, packet_handler=None):
    """포트를 열지 않고 버스에 가짜를 끼운다."""
    bus._port_handler = object()
    bus._packet_handler = packet_handler or FakePacketHandler()
    bus._sync_reader = sync_reader
    return bus


def test_reading_before_connect_is_refused_instead_of_crashing():
    with pytest.raises(MotorError, match="connect"):
        FeetechLiteBus(port="COM99").sync_read_positions()


def test_sync_read_returns_one_value_per_motor():
    raw = {1: 100, 2: 200, 3: 300, 4: 400, 5: 500, 6: 600}
    bus = wire(FeetechLiteBus(port="COM99"), FakeSyncReader(raw))
    assert bus.sync_read_positions() == raw


def test_sync_read_decodes_the_sign_bit():
    """빠뜨리면 음수 위치에서 32,000 같은 값이 그대로 팔로워 목표각이 된다."""
    encoded = encode_sign_magnitude(-1234, SIGN_BIT["Present_Position"])
    assert encoded > 32000, "인코딩된 값 자체는 큰 양수다"
    raw = dict.fromkeys(range(1, 7), encoded)
    bus = wire(FeetechLiteBus(port="COM99"), FakeSyncReader(raw))
    assert bus.sync_read_positions()[1] == -1234


def test_sync_read_retries_a_dropped_packet():
    """1Mbaud 반이중 버스는 패킷을 흘린다. 한 번 실패했다고 포기하면 안 된다."""
    reader = FakeSyncReader(dict.fromkeys(range(1, 7), 2048), fail_times=2)
    bus = wire(FeetechLiteBus(port="COM99"), reader)
    assert bus.sync_read_positions()[1] == 2048
    assert reader.tx_calls == 3


def test_sync_read_gives_up_loudly_rather_than_returning_half_the_arm():
    """일부 모터만 대답하면 나머지는 옛 값이 된다. 조용히 넘기면 안 된다."""
    reader = FakeSyncReader(dict.fromkeys(range(1, 7), 2048), missing={4})
    bus = wire(FeetechLiteBus(port="COM99"), reader)
    with pytest.raises(MotorError, match="no answer"):
        bus.sync_read_positions()


def test_read_decodes_homing_offset_with_its_own_sign_bit():
    """Homing_Offset 은 11번 비트다. Present_Position 의 15번과 섞으면 안 된다."""
    addr, _ = ADDR["Homing_Offset"]
    encoded = encode_sign_magnitude(-1651, SIGN_BIT["Homing_Offset"])
    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler({(1, addr): encoded}))
    assert bus.read("Homing_Offset", 1) == -1651


def test_read_leaves_unsigned_fields_alone():
    addr, _ = ADDR["Present_Temperature"]
    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler({(1, addr): 41}))
    assert bus.read("Present_Temperature", 1) == 41


def test_read_reports_the_motor_id_when_it_never_answers():
    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler(fail_times=99))
    with pytest.raises(MotorError, match="motor 3"):
        bus.read("Present_Voltage", 3)


def test_calibration_comes_from_the_servos_not_a_file():
    """원격 사용자가 JSON 을 정해진 경로에 놓지 않아도 되는 근거다."""
    homing_addr, _ = ADDR["Homing_Offset"]
    min_addr, _ = ADDR["Min_Position_Limit"]
    max_addr, _ = ADDR["Max_Position_Limit"]
    values = {}
    for motor_id in range(1, 7):
        values[(motor_id, homing_addr)] = encode_sign_magnitude(-100 * motor_id, SIGN_BIT["Homing_Offset"])
        values[(motor_id, min_addr)] = 900 + motor_id
        values[(motor_id, max_addr)] = 3100 + motor_id

    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler(values))
    calibration = bus.read_calibration()
    assert set(calibration) == set(range(1, 7))
    assert calibration[3].homing_offset == -300
    assert calibration[3].range_min == 903
    assert calibration[3].range_max == 3103


def test_ping_says_no_instead_of_raising_when_a_motor_is_silent():
    """어느 모터가 죽었는지 세는 진단에 쓰이므로 예외로 멈추면 안 된다."""
    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler(fail_times=99))
    assert bus.ping(4) is False


def test_ping_says_yes_when_the_motor_answers():
    bus = wire(FeetechLiteBus(port="COM99"), packet_handler=FakePacketHandler())
    assert bus.ping(4) is True
