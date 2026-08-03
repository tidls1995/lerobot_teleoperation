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


def test_second_video_server_cannot_steal_the_port():
    """좀비 영상 서버가 같은 포트에 붙으면 '움직이는 옛 화면'이 와서 알아채기 어렵다.

    제어 포트와 같은 이유로 두 번째 기동은 실패해야 한다.
    """
    first = VideoServer(port=0, publishers=[make_publisher()])
    first.start()
    try:
        second = VideoServer(port=first.port, publishers=[make_publisher(cam_id=1)])
        with pytest.raises(OSError, match="still running"):
            second.start()
    finally:
        first.stop()


# --- 2단계: 카메라 1대 실패가 전체를 죽이지 않아야 한다 (스펙 §9) ----------


class UnopenableCamera:
    def open(self):
        raise OSError("device index 999 not present")

    def read(self):
        return None

    def close(self):
        pass


def test_a_camera_that_fails_to_open_is_disabled_not_fatal():
    pub = CameraPublisher(camera=UnopenableCamera(), cam_id=7, fps=15, jpeg_quality=80)
    pub.start()  # 예외가 새어나오면 안 된다
    try:
        assert pub.latest() is None
    finally:
        pub.stop()


def test_other_cameras_keep_streaming_when_one_fails_to_open():
    good = make_publisher(cam_id=0)
    bad = CameraPublisher(camera=UnopenableCamera(), cam_id=1, fps=15, jpeg_quality=80)
    good.start()
    bad.start()
    server = VideoServer(port=0, publishers=[good, bad])
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
            sock.settimeout(5.0)
            hb = recv_exactly(sock, VIDEO_HEADER_SIZE)
            assert hb is not None
            h = VideoHeader.unpack(hb)
            assert h is not None
            assert h.cam_id == 0  # 살아있는 카메라만 나간다
            assert recv_exactly(sock, h.length) is not None
    finally:
        server.stop()
        good.stop()
        bad.stop()


# --- 카메라 열기는 메인 스레드에서 하면 안 된다 ------------------------------
#
# 실측(2026-08-03, 작업대 PC): cv2 를 먼저, lerobot 을 나중에 import 한 프로세스의
# 메인 스레드에서는 cv2.VideoCapture(index, CAP_DSHOW) 가 열리지 않는다.
#   import cv2                        -> True
#   import cv2, lerobot...            -> False   (서버와 같은 순서)
#   import lerobot..., cv2            -> True
# 시리얼 포트 열거의 [WinError 87] 과 같은 뿌리로 보인다. 그때는 SetupAPI,
# 이번엔 DirectShow 다.
#
# 짧은 스레드에서 열어 객체만 넘기는 방식은 쓰지 않는다. DirectShow 는 COM 기반이라
# 만든 스레드의 아파트먼트가 중요하고, 만든 스레드와 읽는 스레드가 다르면 또 다른
# 문제가 생긴다. CameraPublisher 는 이미 자기 캡처 스레드를 가지고 있으므로,
# 열기를 그 스레드 안으로 옮겨 **열기와 읽기가 같은 스레드**에서 일어나게 한다.


class ThreadRecordingCamera:
    def __init__(self):
        import threading

        self.opened_on_main = None
        self.read_on_main = None
        self._threading = threading

    def open(self):
        self.opened_on_main = (
            self._threading.current_thread() is self._threading.main_thread()
        )

    def read(self):
        import numpy as np

        self.read_on_main = (
            self._threading.current_thread() is self._threading.main_thread()
        )
        return np.zeros((48, 64, 3), dtype=np.uint8)

    def close(self):
        pass


def test_the_camera_is_opened_off_the_main_thread():
    cam = ThreadRecordingCamera()
    pub = CameraPublisher(camera=cam, cam_id=0, fps=30, jpeg_quality=70)
    pub.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and pub.latest() is None:
            time.sleep(0.01)
        assert pub.latest() is not None, "프레임이 나오지 않았다"
    finally:
        pub.stop()

    assert cam.opened_on_main is False, "open() 이 메인 스레드에서 불렸다"
    assert cam.read_on_main is False, "read() 도 같은 캡처 스레드여야 한다"


def test_open_and_read_happen_on_the_same_thread():
    """DirectShow 는 COM 아파트먼트에 묶이므로 열기와 읽기가 같은 스레드여야 한다."""
    import threading

    seen = {}

    class SameThreadCamera:
        def open(self):
            seen["open"] = threading.current_thread().name

        def read(self):
            import numpy as np

            seen["read"] = threading.current_thread().name
            return np.zeros((48, 64, 3), dtype=np.uint8)

        def close(self):
            pass

    pub = CameraPublisher(camera=SameThreadCamera(), cam_id=0, fps=30, jpeg_quality=70)
    pub.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and "read" not in seen:
            time.sleep(0.01)
    finally:
        pub.stop()

    assert seen.get("open") == seen.get("read"), f"열기와 읽기가 다른 스레드: {seen}"
