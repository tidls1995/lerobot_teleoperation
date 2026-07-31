import time

import numpy as np
import pytest

from home.video_recv import VideoClient
from mock.fake_cameras import FakeCamera
from workbench.camera_pub import CameraPublisher, VideoServer


def wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def running_server():
    pubs = [
        CameraPublisher(
            camera=FakeCamera(cam_id=i, name=f"cam{i}", width=64, height=48),
            cam_id=i,
            fps=30,
            jpeg_quality=70,
        )
        for i in range(3)
    ]
    for p in pubs:
        p.start()
    server = VideoServer(port=0, publishers=pubs)
    server.start()
    yield server
    server.stop()
    for p in pubs:
        p.stop()


def test_receives_and_decodes_frames_from_all_cameras(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: all(client.latest(i) is not None for i in range(3)))
        frame, t_capture, seq = client.latest(0)
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (48, 64, 3)
        assert t_capture > 0
        assert seq >= 1
    finally:
        client.stop()


def test_reports_connected_state(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    assert client.connected is False
    client.start()
    try:
        assert wait_until(lambda: client.connected)
    finally:
        client.stop()
    assert client.connected is False


def test_frames_keep_advancing(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: client.latest(1) is not None)
        first_seq = client.latest(1)[2]
        assert wait_until(lambda: client.latest(1)[2] > first_seq, timeout=5.0)
    finally:
        client.stop()


def test_reconnects_when_the_server_comes_back(running_server):
    """서버가 죽었다 살아나면 스스로 다시 붙어야 한다."""
    port = running_server.port
    client = VideoClient(host="127.0.0.1", port=port, reconnect_delay=0.1)
    client.start()
    try:
        assert wait_until(lambda: client.connected)
        running_server.stop()
        assert wait_until(lambda: not client.connected, timeout=5.0)

        pub = CameraPublisher(
            camera=FakeCamera(cam_id=0, name="revived", width=64, height=48),
            cam_id=0,
            fps=30,
            jpeg_quality=70,
        )
        pub.start()
        revived = VideoServer(port=port, publishers=[pub])
        revived.start()
        try:
            assert wait_until(lambda: client.connected, timeout=10.0)
        finally:
            revived.stop()
            pub.stop()
    finally:
        client.stop()


def test_start_is_idempotent(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    client.start()
    try:
        assert wait_until(lambda: client.connected)
    finally:
        client.stop()


def test_latest_returns_none_for_unknown_camera(running_server):
    client = VideoClient(host="127.0.0.1", port=running_server.port)
    client.start()
    try:
        assert wait_until(lambda: client.latest(0) is not None)
        assert client.latest(99) is None
    finally:
        client.stop()


def test_connecting_to_nothing_does_not_crash():
    client = VideoClient(host="127.0.0.1", port=1, reconnect_delay=0.05)
    client.start()
    try:
        time.sleep(0.3)
        assert client.connected is False
    finally:
        client.stop()
