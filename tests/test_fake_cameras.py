import numpy as np

from mock.fake_cameras import FakeCamera


def test_frame_has_requested_shape_and_dtype():
    cam = FakeCamera(cam_id=0, name="front", width=320, height=240)
    frame = cam.read()
    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_frame_number_increments_per_read():
    cam = FakeCamera(cam_id=1, name="wrist_left", width=64, height=48)
    cam.read()
    assert cam.frame_number == 1
    cam.read()
    assert cam.frame_number == 2


def test_consecutive_frames_differ():
    """정지 화면이 아니어야 영상이 살아있는지 눈으로 판별할 수 있다."""
    t = [0.0]
    cam = FakeCamera(cam_id=0, name="front", width=160, height=120, clock=lambda: t[0])
    first = cam.read().copy()
    t[0] = 0.5
    second = cam.read()
    assert not np.array_equal(first, second)


def test_different_cameras_render_differently():
    a = FakeCamera(cam_id=0, name="front", width=160, height=120, clock=lambda: 0.0).read()
    b = FakeCamera(cam_id=1, name="wrist_left", width=160, height=120, clock=lambda: 0.0).read()
    assert not np.array_equal(a, b)


def test_frame_encodes_to_jpeg():
    import cv2

    cam = FakeCamera(cam_id=2, name="wrist_right", width=320, height=240)
    ok, buf = cv2.imencode(".jpg", cam.read(), [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    assert ok
    # 320x240 q80 은 대략 5~25KB 범위에 들어온다
    assert 1000 < len(buf) < 60000


def test_close_is_idempotent():
    cam = FakeCamera(cam_id=0, name="front", width=64, height=48)
    cam.close()
    cam.close()
