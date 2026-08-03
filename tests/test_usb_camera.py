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

    def get(self, prop):
        return 0.0  # 아무것도 모르는 장치

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


# --- 픽셀 포맷(FOURCC) --------------------------------------------------------
#
# UVC 카메라가 비압축(YUY2)으로 스트리밍하면 USB 대역폭을 프레임 크기에 비례해
# **미리 예약**한다. 같은 컨트롤러에 여러 대가 붙으면 예약이 고갈되어 나중 것이
# 열리지 않는다. MJPG 는 예약량이 훨씬 작다.
#
# 실측(2026-08-03, 작업대 PC): 서버가 카메라 3대 중 2대만 열었다. 한 대씩 순차로
# 여는 probe 는 4대 다 성공했으므로, 차이는 '동시에 유지'하는 것뿐이다.


class _FourccCapture(_FakeCapture):
    """set() 호출 순서와 FOURCC 를 기억한다. DSHOW 는 설정 순서가 중요하다."""

    def __init__(self, height=240, width=320, fourcc=0x32595559):  # 기본 'YUY2'
        super().__init__(height, width)
        self.calls: list[tuple[int, float]] = []
        self._fourcc = fourcc

    def set(self, prop, value):
        import cv2

        self.calls.append((prop, value))
        if prop == cv2.CAP_PROP_FOURCC:
            self._fourcc = int(value)
        return True

    def get(self, prop):
        import cv2

        if prop == cv2.CAP_PROP_FOURCC:
            return float(self._fourcc)
        return 0.0


def test_fourcc_is_not_touched_by_default(monkeypatch):
    """포맷을 지정하지 않으면 드라이버가 고른 것을 그대로 쓴다."""
    import cv2

    import workbench.usb_camera as mod

    cap = _FourccCapture()
    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = UsbCamera(cam_id=0, name="c", index=0, width=320, height=240, fps=15)
    cam.open()
    cam.close()

    assert not any(prop == cv2.CAP_PROP_FOURCC for prop, _ in cap.calls)


def test_fourcc_is_set_before_the_resolution(monkeypatch):
    """DSHOW 에서는 포맷을 해상도보다 먼저 정해야 원하는 조합이 잡힌다.

    순서를 뒤집으면 해상도만 반영되고 포맷은 드라이버 기본(보통 YUY2)으로 남는다.
    """
    import cv2

    import workbench.usb_camera as mod

    cap = _FourccCapture()
    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = UsbCamera(cam_id=0, name="c", index=0, width=320, height=240, fps=15, fourcc="MJPG")
    cam.open()
    cam.close()

    props = [prop for prop, _ in cap.calls]
    assert cv2.CAP_PROP_FOURCC in props, "포맷을 지정했는데 설정하지 않았다"
    assert props.index(cv2.CAP_PROP_FOURCC) < props.index(cv2.CAP_PROP_FRAME_WIDTH)


def test_actual_fourcc_reports_what_the_device_negotiated(monkeypatch):
    """요청과 실제가 다를 수 있으므로 실제 값을 읽어 보고한다."""
    import workbench.usb_camera as mod

    cap = _FourccCapture(fourcc=0x32595559)  # 'YUY2'
    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = UsbCamera(cam_id=0, name="c", index=0, width=320, height=240, fps=15)
    cam.open()
    try:
        assert cam.actual_fourcc == "YUY2"
    finally:
        cam.close()


def test_actual_fourcc_is_none_before_open():
    assert make().actual_fourcc is None


def test_a_device_that_ignores_the_format_request_is_reported_honestly(monkeypatch):
    """MJPG 를 요청했는데 YUY2 로 남으면 그 사실이 보여야 한다.

    그렇지 않으면 '포맷을 바꿨는데도 안 열린다'와 '포맷이 안 바뀌었다'를 구분할 수
    없어, 또 엉뚱한 곳을 고치게 된다.
    """
    import cv2

    import workbench.usb_camera as mod

    class Stubborn(_FourccCapture):
        def set(self, prop, value):
            self.calls.append((prop, value))
            if prop == cv2.CAP_PROP_FOURCC:
                return False  # 요청을 거부하고 YUY2 를 유지한다
            return True

    cap = Stubborn(fourcc=0x32595559)
    monkeypatch.setattr(mod.cv2, "VideoCapture", lambda *a, **k: cap)
    cam = UsbCamera(cam_id=0, name="c", index=0, width=320, height=240, fps=15, fourcc="MJPG")
    cam.open()
    try:
        assert cam.actual_fourcc == "YUY2"
    finally:
        cam.close()
