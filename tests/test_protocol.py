import struct

import pytest

from common.protocol import (
    CONTROL_SIZE,
    N_JOINTS,
    PROTOCOL_MAGIC,
    TELEMETRY_SIZE,
    VIDEO_HEADER_SIZE,
    VIDEO_MAGIC,
    Cmd,
    ControlPacket,
    Flag,
    JOINT_NAMES,
    State,
    TelemetryPacket,
    VideoHeader,
    is_newer,
)

ANGLES = tuple(float(i) * 1.5 - 8.0 for i in range(N_JOINTS))


def test_joint_names_count_matches_n_joints():
    assert len(JOINT_NAMES) == N_JOINTS
    assert len(set(JOINT_NAMES)) == N_JOINTS


def test_control_packet_size_is_66():
    p = ControlPacket(seq=1, t_send=1.0, clutch=True, cmd=Cmd.NONE, joints=ANGLES)
    assert len(p.pack()) == CONTROL_SIZE == 66


def test_control_packet_roundtrip():
    p = ControlPacket(seq=123456, t_send=1753502841.5, clutch=True, cmd=Cmd.RESET, joints=ANGLES)
    got = ControlPacket.unpack(p.pack())
    assert got is not None
    assert got.seq == p.seq
    assert got.clutch is True
    assert got.cmd == Cmd.RESET
    assert got.t_send == pytest.approx(p.t_send, abs=1e-6)
    assert got.joints == pytest.approx(ANGLES, abs=1e-3)


def test_control_packet_rejects_wrong_magic():
    data = bytearray(ControlPacket(1, 1.0, False, Cmd.NONE, ANGLES).pack())
    data[0:4] = b"XXXX"
    assert ControlPacket.unpack(bytes(data)) is None


def test_control_packet_rejects_truncated():
    data = ControlPacket(1, 1.0, False, Cmd.NONE, ANGLES).pack()
    assert ControlPacket.unpack(data[:-1]) is None
    assert ControlPacket.unpack(data + b"\x00") is None


def test_telemetry_packet_size_and_roundtrip():
    flags = int(Flag.SPEED_CLAMPED | Flag.FOLLOW_ERROR)
    p = TelemetryPacket(seq_echo=99, t_send=2.5, state=State.ENGAGED, flags=flags, joints=ANGLES)
    assert len(p.pack()) == TELEMETRY_SIZE == 66
    got = TelemetryPacket.unpack(p.pack())
    assert got is not None
    assert got.seq_echo == 99
    assert got.state is State.ENGAGED
    assert got.flags == flags
    assert got.joints == pytest.approx(ANGLES, abs=1e-3)


def test_video_header_size_and_roundtrip():
    h = VideoHeader(cam_id=2, seq=7, t_capture=10.25, length=12345)
    assert len(h.pack()) == VIDEO_HEADER_SIZE == 21
    got = VideoHeader.unpack(h.pack())
    assert got is not None
    assert (got.cam_id, got.seq, got.length) == (2, 7, 12345)
    assert got.t_capture == pytest.approx(10.25)


def test_video_header_rejects_wrong_magic():
    data = bytearray(VideoHeader(0, 1, 1.0, 10).pack())
    data[0:4] = b"ZZZZ"
    assert VideoHeader.unpack(bytes(data)) is None


def test_magics_are_distinct_four_bytes():
    assert PROTOCOL_MAGIC == b"RT01"
    assert VIDEO_MAGIC == b"RTV1"
    assert len(PROTOCOL_MAGIC) == len(VIDEO_MAGIC) == 4


def test_control_packet_rejects_wrong_joint_count():
    with pytest.raises(ValueError):
        ControlPacket(1, 1.0, False, Cmd.NONE, (0.0,) * 5).pack()


def test_is_newer_normal_progression():
    assert is_newer(11, 10) is True
    assert is_newer(10, 10) is False
    assert is_newer(9, 10) is False


def test_is_newer_handles_uint32_wraparound():
    # 42억을 넘어 0으로 되감긴 경우도 '더 새로움'으로 판정해야 한다
    assert is_newer(2, 0xFFFFFFFE) is True
    assert is_newer(0xFFFFFFFE, 2) is False


def test_state_and_flag_values_match_spec():
    assert (State.DISCONNECTED, State.ALIGNING, State.ENGAGED, State.HOLD, State.FAULT) == (0, 1, 2, 3, 4)
    assert (Flag.SPEED_CLAMPED, Flag.JOINT_LIMITED, Flag.FOLLOW_ERROR) == (1, 2, 4)
    assert (Flag.WATCHDOG, Flag.MOTOR_ERROR) == (8, 16)
