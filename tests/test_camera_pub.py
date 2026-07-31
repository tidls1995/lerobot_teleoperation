import socket
import time

import numpy as np
import pytest

from common.netutil import recv_exactly
from common.protocol import VIDEO_HEADER_SIZE, VideoHeader
from mock.fake_cameras import FakeCamera
from workbench.camera_pub import CameraPublisher, VideoServer


def make_publisher(cam_id=0, **kwargs):
    cam = FakeCamera(cam_id=cam_id, name=f"cam{cam_id}", width=64, height=48)
    return CameraPublisher(camera=cam, cam_id=cam_id, fps=15, jpeg_quality=80, **kwargs)


def test_latest_is_none_before_any_capture():
    assert make_publisher().latest() is None


def test_capture_once_produces_a_jpeg():
    pub = make_publisher()
    pub.capture_once()
    got = pub.latest()
    assert got is not None
    jpeg, t_capture, seq = got
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI 마커
    assert seq == 1
    assert t_capture > 0


def test_sequence_increments_and_old_frame_is_discarded():
    """1슬롯 버퍼: 새로 찍으면 이전 프레임은 사라진다."""
    pub = make_publisher()
    pub.capture_once()
    first_jpeg, _, first_seq = pub.latest()
    pub.capture_once()
    second_jpeg, _, second_seq = pub.latest()
    assert second_seq == first_seq + 1
    assert second_jpeg != first_jpeg


def test_video_server_streams_frames_to_a_connected_client():
    pub = make_publisher(cam_id=1)
    pub.capture_once()
    server = VideoServer(port=0, publishers=[pub])
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
            assert header_bytes is not None
            header = VideoHeader.unpack(header_bytes)
            assert header is not None
            assert header.cam_id == 1
            assert header.length > 0
            payload = recv_exactly(sock, header.length)
            assert payload is not None
            assert len(payload) == header.length
            assert payload[:2] == b"\xff\xd8"
    finally:
        server.stop()


def test_video_server_multiplexes_all_cameras_on_one_connection():
    pubs = [make_publisher(cam_id=i) for i in range(3)]
    for p in pubs:
        p.start()
    server = VideoServer(port=0, publishers=pubs)
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            seen = set()
            deadline = time.monotonic() + 10.0
            while len(seen) < 3 and time.monotonic() < deadline:
                hb = recv_exactly(sock, VIDEO_HEADER_SIZE)
                assert hb is not None
                h = VideoHeader.unpack(hb)
                assert h is not None
                assert recv_exactly(sock, h.length) is not None
                seen.add(h.cam_id)
            assert seen == {0, 1, 2}
    finally:
        server.stop()
        for p in pubs:
            p.stop()


def test_new_connection_replaces_the_old_one():
    pub = make_publisher()
    pub.start()
    server = VideoServer(port=0, publishers=[pub])
    server.start()
    try:
        first = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
        first.settimeout(5.0)
        assert recv_exactly(first, VIDEO_HEADER_SIZE) is not None

        second = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
        second.settimeout(5.0)
        assert recv_exactly(second, VIDEO_HEADER_SIZE) is not None

        # 새 연결이 붙었으므로 이전 연결은 곧 닫힌다
        deadline = time.monotonic() + 5.0
        closed = False
        while time.monotonic() < deadline:
            try:
                if first.recv(4096) == b"":
                    closed = True
                    break
            except OSError:
                closed = True
                break
        assert closed
        first.close()
        second.close()
    finally:
        server.stop()
        pub.stop()


def test_capture_thread_runs_at_roughly_the_requested_fps():
    cam = FakeCamera(cam_id=0, name="c", width=64, height=48)
    pub = CameraPublisher(camera=cam, cam_id=0, fps=30, jpeg_quality=60)
    pub.start()
    try:
        time.sleep(0.5)
        got = pub.latest()
        assert got is not None
        _, _, seq = got
        # 0.5초 * 30fps = 약 15장. 타이밍 여유를 크게 준다.
        assert 5 <= seq <= 40
    finally:
        pub.stop()
