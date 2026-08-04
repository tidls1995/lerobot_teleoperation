"""lerobot 없이 Feetech STS3215 서보를 읽는다.

**왜 따로 만드는가.** `lerobot.motors.feetech` 를 import 하면 `torch`(4.2GB)와
`pandas` 가 딸려온다. 정책 학습에 쓰는 것들이고 조종에는 한 줄도 쓰이지 않는데,
그대로 PyInstaller 로 묶으면 몇 GB 짜리 exe 가 되어 원격 사용자에게 배포할 수 없다.
실제로 모터와 말하는 것은 `scservo_sdk` 0.1MB 뿐이다.

**캘리브레이션 파일이 필요 없다.** lerobot 의 `write_calibration` 은 `Homing_Offset`,
`Min_Position_Limit`, `Max_Position_Limit` 를 **서보 EEPROM 에 쓴다.** 그래서 값은
모터가 들고 있고, 우리는 물어보기만 하면 된다. 원격 사용자가 JSON 을 정해진 경로에
놓을 필요가 없다 - 그 함정은 이미 한 번 우리를 물었다 (hardware-setup 5-3).

**숫자는 lerobot 과 정확히 같아야 한다.** 서버(팔로워)는 계속 lerobot 을 쓰므로,
여기 계산이 조금이라도 다르면 리더와 팔로워가 서로 다른 각도를 말하게 되고 정렬이
끝나지 않거나 어긋난 채로 조종이 시작된다. 아래 상수와 공식은 lerobot 에서 그대로
옮긴 것이고, `tools/compare_read.py` 가 실물에서 두 방식을 대조한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: STS3215 제어 테이블. lerobot 의 STS_SMS_SERIES_CONTROL_TABLE 에서 옮겼다.
#: (주소, 바이트 수)
ADDR = {
    "Min_Position_Limit": (9, 2),
    "Max_Position_Limit": (11, 2),
    "Homing_Offset": (31, 2),
    "Torque_Enable": (40, 1),
    "Goal_Position": (42, 2),
    "Lock": (55, 1),
    "Present_Position": (56, 2),
    "Present_Load": (60, 2),
    "Present_Voltage": (62, 1),
    "Present_Temperature": (63, 1),
}

#: 부호-크기 인코딩을 쓰는 항목과 그 부호 비트. lerobot 의 MODEL_ENCODING_TABLE.
#:
#: **이것을 빠뜨리면 조용히 틀린다.** 그냥 2바이트 정수로 읽으면 음수 위치에서
#: 32,000 같은 값이 나오고, 그 값이 그대로 팔로워 목표각이 된다.
SIGN_BIT = {
    "Homing_Offset": 11,
    "Goal_Position": 15,
    "Present_Position": 15,
    "Present_Load": 10,
}

#: STS3215 의 분해능. 각도 변환의 분모(resolution - 1)로 쓴다.
RESOLUTION = 4096

DEFAULT_BAUDRATE = 1_000_000

#: SO-101 의 모터 순서. id 1~6.
MOTOR_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


class MotorError(Exception):
    """모터와 말할 수 없다."""


@dataclass(frozen=True)
class MotorCalibration:
    """모터 하나의 캘리브레이션. 서보 EEPROM 에서 읽어온다."""

    id: int
    homing_offset: int
    range_min: int
    range_max: int


# --- 순수 계산. 하드웨어 없이 검증할 수 있어야 한다 ----------------------------


def decode_sign_magnitude(encoded: int, sign_bit_index: int) -> int:
    direction = (encoded >> sign_bit_index) & 1
    magnitude = encoded & ((1 << sign_bit_index) - 1)
    return -magnitude if direction else magnitude


def encode_sign_magnitude(value: int, sign_bit_index: int) -> int:
    max_magnitude = (1 << sign_bit_index) - 1
    magnitude = abs(value)
    if magnitude > max_magnitude:
        raise ValueError(f"magnitude {magnitude} exceeds {max_magnitude} for sign bit {sign_bit_index}")
    return ((1 if value < 0 else 0) << sign_bit_index) | magnitude


def raw_to_degrees(raw: int, cal: MotorCalibration, resolution: int = RESOLUTION) -> float:
    """lerobot 의 MotorNormMode.DEGREES 와 같은 계산.

    **값을 자르지 않는다.** lerobot 도 자르지 않는다 - 캘리브레이션 때 훑은 범위는
    기계적 한계가 아니라 그때 훑은 만큼일 뿐이어서, 그보다 큰 값이 정상적으로 읽힌다
    (실측: right_elbow_flex 가 기록 ±96.7 을 넘어 97.1 로 읽혔다). 여기서 자르면
    lerobot 을 쓰는 팔로워와 숫자가 어긋난다.
    """
    mid = (cal.range_min + cal.range_max) / 2
    return (raw - mid) * 360.0 / (resolution - 1)


def degrees_to_raw(degrees: float, cal: MotorCalibration, resolution: int = RESOLUTION) -> int:
    mid = (cal.range_min + cal.range_max) / 2
    return int((degrees * (resolution - 1) / 360.0) + mid)


def raw_to_percent(raw: int, cal: MotorCalibration) -> float:
    """lerobot 의 MotorNormMode.RANGE_0_100 과 같은 계산. 그리퍼에 쓴다.

    이쪽은 **자른다** - lerobot 이 bounded_val 을 쓰기 때문이다. 각도와 다르게
    동작하는 것이 헷갈리지만, 팔로워와 맞추는 것이 우선이다.
    """
    if cal.range_max == cal.range_min:
        raise ValueError(f"motor {cal.id}: range_min and range_max are equal, calibration is unusable")
    bounded = min(cal.range_max, max(cal.range_min, raw))
    return (bounded - cal.range_min) / (cal.range_max - cal.range_min) * 100.0


def percent_to_raw(percent: float, cal: MotorCalibration) -> int:
    if cal.range_max == cal.range_min:
        raise ValueError(f"motor {cal.id}: range_min and range_max are equal, calibration is unusable")
    bounded = min(100.0, max(0.0, percent))
    return int(bounded / 100.0 * (cal.range_max - cal.range_min) + cal.range_min)


def looks_uncalibrated(cal: MotorCalibration, resolution: int = RESOLUTION) -> bool:
    """캘리브레이션이 공장 기본값처럼 보이는가.

    캘리브레이션을 한 적 없는 모터는 범위가 0~4095 로 남아 있다. 그대로 조종을
    시작하면 각도가 엉뚱하게 나와 팔로워가 이상한 자세로 간다. 사람에게 먼저
    알려주기 위한 판정이다.

    **범위만 보면 안 된다.** `wrist_roll` 은 한 바퀴를 도는 관절이라 캘리브레이션을
    제대로 해도 0~4095 가 나온다. 범위만으로 판정하면 정상인 팔마다 경고가 뜨고,
    그러면 사람이 경고를 무시하게 되어 진짜 미캘리브레이션도 놓친다.

    그래서 `homing_offset` 도 함께 본다. 캘리브레이션은 영점을 잡아 이 값을 서보에
    쓰므로, 범위가 공장값인데 영점까지 0 이면 손댄 적이 없다고 볼 수 있다.
    """
    factory_range = cal.range_min == 0 and cal.range_max == resolution - 1
    return factory_range and cal.homing_offset == 0


# --- 하드웨어 -----------------------------------------------------------------


def _patch_packet_timeout(port_handler) -> None:
    """PyPI 의 비공식 scservo_sdk 는 패킷 타임아웃 계산이 틀려 있다.

    lerobot 도 같은 곳을 고쳐 쓴다 (feetech.py 의 patch_setPacketTimeout).
    고치지 않으면 읽기가 느려지거나 간헐적으로 실패한다.
    """

    def set_packet_timeout(self, packet_length):
        self.packet_start_time = self.getCurrentTime()
        self.packet_timeout = (self.tx_time_per_byte * packet_length) + (self.tx_time_per_byte * 3.0) + 50

    import scservo_sdk as scs

    port_handler.setPacketTimeout = set_packet_timeout.__get__(port_handler, scs.PortHandler)


class FeetechLiteBus:
    """한 시리얼 포트에 데이지체인된 STS3215 서보들.

    lerobot 의 `FeetechMotorsBus` 와 같은 일을 하되, 리더 암에 필요한 것만 한다:
    위치 읽기, 캘리브레이션 읽기, 상태 읽기, 그리고 **토크 끄기**.

    `Goal_Position` 은 쓰지 않는다. 리더는 사람이 손으로 움직이는 것이라 서보에
    자세를 명령할 일이 없고, 없는 기능은 잘못 쓰일 일도 없다. 토크 끄기는 반대로
    **꼭 필요하다** - 켜진 채로는 사람이 팔을 움직일 수 없다.
    """

    def __init__(
        self,
        port: str,
        ids: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        baudrate: int = DEFAULT_BAUDRATE,
    ) -> None:
        self.port = port
        self.ids = tuple(ids)
        self.baudrate = baudrate
        self._port_handler = None
        self._packet_handler = None
        self._sync_reader = None

    @property
    def is_open(self) -> bool:
        return self._port_handler is not None

    def connect(self) -> None:
        import scservo_sdk as scs

        port_handler = scs.PortHandler(self.port)
        _patch_packet_timeout(port_handler)
        if not port_handler.openPort():
            raise MotorError(f"cannot open {self.port}. Is another program using it?")
        if not port_handler.setBaudRate(self.baudrate):
            port_handler.closePort()
            raise MotorError(f"cannot set {self.baudrate} baud on {self.port}")

        self._port_handler = port_handler
        self._packet_handler = scs.PacketHandler(0)  # STS 계열은 프로토콜 0
        addr, length = ADDR["Present_Position"]
        self._sync_reader = scs.GroupSyncRead(port_handler, self._packet_handler, addr, length)
        for motor_id in self.ids:
            self._sync_reader.addParam(motor_id)

    def close(self) -> None:
        if self._port_handler is not None:
            self._port_handler.closePort()
            self._port_handler = None
            self._packet_handler = None
            self._sync_reader = None

    def _require_open(self) -> None:
        if self._port_handler is None:
            raise MotorError("connect() must be called first")

    def ping(self, motor_id: int, retries: int = 2) -> bool:
        """이 모터가 대답하는가. 배선과 전원을 가른다."""
        self._require_open()
        import scservo_sdk as scs

        for _ in range(retries + 1):
            _, result, _ = self._packet_handler.ping(self._port_handler, motor_id)
            if result == scs.COMM_SUCCESS:
                return True
        return False

    def read(self, name: str, motor_id: int, retries: int = 2) -> int:
        """제어 테이블 항목 하나를 읽는다. 부호 인코딩이 있으면 풀어서 준다."""
        self._require_open()
        import scservo_sdk as scs

        addr, length = ADDR[name]
        reader = self._packet_handler.read1ByteTxRx if length == 1 else self._packet_handler.read2ByteTxRx

        result = scs.COMM_RX_FAIL
        for _ in range(retries + 1):
            value, result, _ = reader(self._port_handler, motor_id, addr)
            if result == scs.COMM_SUCCESS:
                sign_bit = SIGN_BIT.get(name)
                return decode_sign_magnitude(value, sign_bit) if sign_bit is not None else value
        raise MotorError(
            f"motor {motor_id}: cannot read {name} - {self._packet_handler.getTxRxResult(result)}"
        )

    def write(self, name: str, motor_id: int, value: int, retries: int = 2) -> None:
        """제어 테이블 항목 하나를 쓴다. 부호 인코딩이 필요하면 씌워서 보낸다."""
        self._require_open()
        import scservo_sdk as scs

        addr, length = ADDR[name]
        sign_bit = SIGN_BIT.get(name)
        payload = encode_sign_magnitude(value, sign_bit) if sign_bit is not None else value
        writer = (
            self._packet_handler.write1ByteTxRx if length == 1 else self._packet_handler.write2ByteTxRx
        )

        result = scs.COMM_RX_FAIL
        for _ in range(retries + 1):
            # 쓰기는 (result, error) 2-튜플이다. 읽기의 3-튜플과 다르다.
            result, _ = writer(self._port_handler, motor_id, addr, payload)
            if result == scs.COMM_SUCCESS:
                return
        raise MotorError(
            f"motor {motor_id}: cannot write {name} - {self._packet_handler.getTxRxResult(result)}"
        )

    def disable_torque(self, retries: int = 5) -> None:
        """모든 모터의 토크를 끈다. 켜진 채로는 사람이 리더를 움직일 수 없다.

        재시도를 넉넉히 준다. lerobot 은 이 자리에 `num_retry=0` 을 써서 1Mbaud
        반이중 버스에서 패킷 하나만 유실돼도 연결이 죽었다 (hardware-setup 결함 1).
        """
        for motor_id in self.ids:
            self.write("Torque_Enable", motor_id, 0, retries=retries)
            self.write("Lock", motor_id, 0, retries=retries)

    def read_calibration(self) -> dict[int, MotorCalibration]:
        """서보 EEPROM 에서 캘리브레이션을 읽는다. **파일이 필요 없다.**"""
        self._require_open()
        out: dict[int, MotorCalibration] = {}
        for motor_id in self.ids:
            out[motor_id] = MotorCalibration(
                id=motor_id,
                homing_offset=self.read("Homing_Offset", motor_id),
                range_min=self.read("Min_Position_Limit", motor_id),
                range_max=self.read("Max_Position_Limit", motor_id),
            )
        return out

    def sync_read_positions(self, retries: int = 2) -> dict[int, int]:
        """모든 모터의 Present_Position 을 한 번에 읽는다 (부호 해제된 raw)."""
        self._require_open()
        import scservo_sdk as scs

        addr, length = ADDR["Present_Position"]
        for _ in range(retries + 1):
            if self._sync_reader.txRxPacket() != scs.COMM_SUCCESS:
                continue
            # isAvailable 은 bool 을 돌려준다 (dynamixel SDK 처럼 튜플이 아니다).
            if not all(self._sync_reader.isAvailable(i, addr, length) for i in self.ids):
                continue
            return {
                motor_id: decode_sign_magnitude(
                    self._sync_reader.getData(motor_id, addr, length), SIGN_BIT["Present_Position"]
                )
                for motor_id in self.ids
            }
        raise MotorError(
            f"{self.port}: no answer from all of {list(self.ids)}. "
            "Check power and the daisy-chain cable."
        )

    def read_health(self) -> dict[int, dict[str, int]]:
        """토크 걸림 여부, 부하, 전압, 온도. 사람이 보고 판단할 값들이다."""
        self._require_open()
        out: dict[int, dict[str, int]] = {}
        for motor_id in self.ids:
            out[motor_id] = {
                "torque": self.read("Torque_Enable", motor_id),
                "load": self.read("Present_Load", motor_id),
                "volts_x10": self.read("Present_Voltage", motor_id),
                "temp_c": self.read("Present_Temperature", motor_id),
            }
        return out
