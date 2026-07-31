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
