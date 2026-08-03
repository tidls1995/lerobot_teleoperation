import pytest

from common.devices import Camera
from workbench.usb_camera import CameraOpenError, UsbCamera


def make(index=999):
    return UsbCamera(cam_id=0, name="probe", index=index, width=320, height=240, fps=15)


def test_satisfies_camera_protocol():
    assert isinstance(make(), Camera)


def test_constructing_does_not_open_the_device():
    assert make().is_open is False


def test_read_before_open_returns_none():
    """카메라 1대가 죽어도 나머지가 계속 돌아야 한다 (스펙 §9). 예외를 던지지 않는다."""
    assert make().read() is None


def test_opening_a_nonexistent_index_raises():
    # 인덱스 999 에 카메라가 있을 수는 없다
    with pytest.raises(CameraOpenError, match="999"):
        make(index=999).open()


def test_close_before_open_is_harmless():
    make().close()
    make().close()


def test_actual_size_is_none_before_open():
    assert make().actual_size is None


# --- 장치가 해상도 요청을 거부하면 받아서 줄인다 -----------------------------
#
# 실측(2026-08-03, 작업대 PC): 카메라 한 대가 320x240 요청을 무시하고 1280x720 을
# 줬다. 그대로 보내면 그 한 대가 8배 데이터를 차지해 설정값이 무의미해진다.
# 4단계 인터넷에서는 작업대 업로드 대역폭을 쓰므로 더 문제가 된다.


class _FakeCapture:
    """cv2.VideoCapture 를 대신해, 요청과 다른 크기를 주는 장치를 흉내낸다."""

    def __init__(self, height, width):
        import numpy as np

        self._frame = np.zeros((height, width, 3), dtype=np.uint8)
        self.released = False

    def isOpened(self):
        return True

    def set(self, prop, value):
        return True

    def read(self):
        return True, self._frame.copy()

    def release(self):
        self.released = True


def test_a_frame_larger_than_requested_is_scaled_down(monkeypatch):
    import workbench.usb_camera as mod

    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: _FakeCapture(720, 1280))
    cam = UsbCamera(cam_id=0, name="stubborn", index=0, width=320, height=240, fps=15)
    cam.open()
    try:
        assert cam.actual_size == (1280, 720), "장치가 실제로 준 크기는 그대로 보고해야 한다"
        frame = cam.read()
        assert frame.shape[:2] == (240, 320), "설정한 크기로 줄여서 내보내야 한다"
    finally:
        cam.close()


def test_a_frame_already_the_right_size_is_not_touched(monkeypatch):
    import workbench.usb_camera as mod

    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: _FakeCapture(240, 320))
    cam = UsbCamera(cam_id=0, name="obedient", index=0, width=320, height=240, fps=15)
    cam.open()
    try:
        assert cam.actual_size == (320, 240)
        assert cam.read().shape[:2] == (240, 320)
    finally:
        cam.close()
