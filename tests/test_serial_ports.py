import pytest

from common.serial_ports import (
    PortInfo,
    PortLookupError,
    describe_ports,
    find_port_by_serial,
    list_serial_ports,
    resolve_port_spec,
)

PORTS = [
    PortInfo(device="COM3", serial_number="AB12CD34", description="USB Serial"),
    PortInfo(device="COM4", serial_number="EF56GH78", description="USB Serial"),
    PortInfo(device="COM5", serial_number=None, description="Bluetooth"),
]


def test_finds_port_by_serial_number():
    assert find_port_by_serial("EF56GH78", PORTS) == "COM4"


def test_serial_number_match_is_case_insensitive():
    assert find_port_by_serial("ef56gh78", PORTS) == "COM4"


def test_unknown_serial_raises_and_lists_what_was_found():
    with pytest.raises(PortLookupError) as exc:
        find_port_by_serial("NOPE", PORTS)
    message = str(exc.value)
    assert "NOPE" in message
    # 오류 메시지가 실제로 붙어 있는 장치를 알려줘야 사용자가 고칠 수 있다
    assert "AB12CD34" in message
    assert "COM3" in message


def test_duplicate_serial_numbers_are_rejected():
    dupes = [
        PortInfo(device="COM3", serial_number="SAME", description="USB Serial"),
        PortInfo(device="COM9", serial_number="SAME", description="USB Serial"),
    ]
    with pytest.raises(PortLookupError, match="more than one"):
        find_port_by_serial("SAME", dupes)


def test_ports_without_serial_numbers_are_ignored_not_matched():
    with pytest.raises(PortLookupError):
        find_port_by_serial("None", PORTS)


def test_empty_serial_number_is_rejected():
    with pytest.raises(PortLookupError, match="empty"):
        find_port_by_serial("", PORTS)


def test_describe_ports_lists_every_port():
    text = describe_ports(PORTS)
    for expected in ("COM3", "COM4", "COM5", "AB12CD34", "Bluetooth"):
        assert expected in text


def test_describe_ports_handles_no_ports():
    assert "no serial ports" in describe_ports([]).lower()


def test_list_serial_ports_returns_port_infos():
    """실제 장치가 없어도 호출 자체는 성공해야 한다 (빈 목록이면 빈 목록)."""
    ports = list_serial_ports()
    assert isinstance(ports, list)
    for p in ports:
        assert isinstance(p, PortInfo)
        assert isinstance(p.device, str)


def test_resolve_port_spec_prefers_explicit_port():
    assert resolve_port_spec(serial_number=None, port="COM12", ports=PORTS) == "COM12"


def test_resolve_port_spec_looks_up_serial_number():
    assert resolve_port_spec(serial_number="EF56GH78", port=None, ports=PORTS) == "COM4"


def test_resolve_port_spec_rejects_neither():
    with pytest.raises(PortLookupError, match="exactly one"):
        resolve_port_spec(serial_number=None, port=None, ports=PORTS)


def test_resolve_port_spec_rejects_both():
    with pytest.raises(PortLookupError, match="exactly one"):
        resolve_port_spec(serial_number="AB12CD34", port="COM3", ports=PORTS)
