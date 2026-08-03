"""USB 시리얼 번호로 COM 포트를 찾는다.

설정 파일에 COM 번호를 직접 적으면 안 된다. Windows 의 COM 번호는 USB 를 다시
꽂거나 재부팅하면 뒤바뀌고, 좌우 팔이 뒤바뀐 채로 조종을 시작하면 조종자가
왼쪽을 움직였는데 오른쪽 팔이 장비를 치는 상황이 된다 (스펙 §7.2).

최초에 어느 시리얼 번호가 어느 팔인지 알아내려면 `lerobot-find-port` 를 쓰거나,
`describe_ports()` 를 출력해 놓고 팔을 하나씩 뽑아 보면 된다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from serial.tools import list_ports

log = logging.getLogger(__name__)

#: 장치 열거를 몇 번까지 다시 시도할 것인가.
#:
#: 아래 ``[WinError 87]`` 과는 무관하다. 그 고장은 재시도로 낫지 않는다
#: (실측: 10초간 50번 재시도해도 50번 다 실패). 진짜 일시적인 실패에만 쓰인다.
_LIST_RETRIES = 4
_LIST_RETRY_DELAY = 0.3


def _comports_off_main_thread() -> list:
    """``comports()`` 를 **새 스레드에서** 호출한다.

    실측(2026-08-03, 작업대 PC). 서버가 포트 조회에서
    ``OSError: [WinError 87] 매개 변수가 틀립니다`` 로 죽었다. pyserial 이 죽는
    지점은 장치를 열거하기도 전, 클래스 이름 "Ports" 를 GUID 로 바꾸는
    ``SetupDiClassGuidsFromNameW`` 호출이다. 측정으로 확인한 것:

    * **메인 스레드만 고장난다.** 같은 프로세스에서 새 스레드로 부르면 성공하고,
      그 직후 메인 스레드로 부르면 실패한다.
    * cv2 와 lerobot.motors 가 **둘 다** 로드된 뒤 그 스레드에서 처음 SetupAPI 를
      부를 때만 발생한다. 둘 중 하나만으로는 재현되지 않는다.
    * 한 번 성공한 스레드는 그 뒤로 계속 성공한다. 그래서 시작하자마자 열거해 보는
      진단 도구들은 전부 통과했고 (probe_startup, --diagnose), 그 통과가 오히려
      원인을 가렸다.
    * 재시도로는 낫지 않고, 열린 포트나 USB 재삽입과도 무관하다.

    cv2 와 torch 가 끌어오는 수십 개의 DLL 이 이미 만들어져 있던 메인 스레드의
    스레드 로컬 저장소를 고갈시키는, Windows 에서 알려진 증상이다. 새로 만든
    스레드는 그 DLL 들이 다 올라온 뒤에 생기므로 영향을 받지 않는다.

    import 순서를 바꿔서(lerobot 을 cv2 보다 먼저) 피할 수도 있지만, 그건 누가
    import 한 줄을 옮기면 조용히 되돌아온다. 조회를 스레드로 옮기는 편이
    호출자에게 영향이 없고 순서에 기대지 않는다.
    """
    box: dict[str, object] = {}

    def work() -> None:
        try:
            box["ports"] = list_ports.comports()
        except BaseException as exc:  # noqa: BLE001 - 호출 스레드로 그대로 넘긴다
            box["error"] = exc

    thread = threading.Thread(target=work, name="list-serial-ports", daemon=True)
    thread.start()
    thread.join()

    error = box.get("error")
    if error is not None:
        raise error  # type: ignore[misc]
    return box["ports"]  # type: ignore[return-value]


class PortLookupError(Exception):
    """지정한 시리얼 번호를 가진 포트를 특정할 수 없다."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    serial_number: str | None
    description: str


def list_serial_ports(
    retries: int = _LIST_RETRIES, delay: float = _LIST_RETRY_DELAY
) -> list[PortInfo]:
    """현재 붙어 있는 시리얼 포트 목록.

    장치 열거는 간헐적으로 실패할 수 있다. 일시적 실패로 서버가 통째로
    내려가면 안 되므로 다시 시도한다. 조회 자체를 왜 새 스레드에서 하는지는
    ``_comports_off_main_thread`` 를 보라.

    Raises:
        PortLookupError: 여러 번 시도해도 목록을 못 읽었을 때.
    """
    last_error: OSError | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = _comports_off_main_thread()
        except OSError as exc:
            last_error = exc
            log.warning(
                "listing serial ports failed (attempt %d/%d): %s", attempt, retries, exc
            )
            time.sleep(delay)
            continue
        return [
            PortInfo(
                device=p.device,
                serial_number=p.serial_number,
                description=p.description or "",
            )
            for p in raw
        ]

    raise PortLookupError(
        f"could not enumerate serial ports after {retries} attempts: {last_error}. "
        "Unplug and replug the USB adapters, then try again."
    ) from last_error


def describe_ports(ports: list[PortInfo] | None = None) -> str:
    """사람이 읽을 수 있는 포트 목록. 오류 메시지와 진단 스크립트가 함께 쓴다."""
    ports = list_serial_ports() if ports is None else ports
    if not ports:
        return "no serial ports found"
    lines = [
        f"  {p.device:8s} serial={p.serial_number or '(none)':20s} {p.description}" for p in ports
    ]
    return "\n".join(lines)


def find_port_by_serial(serial_number: str, ports: list[PortInfo] | None = None) -> str:
    """시리얼 번호에 해당하는 COM 포트를 돌려준다.

    Raises:
        PortLookupError: 못 찾았거나, 같은 번호가 둘 이상일 때.
    """
    if not serial_number:
        raise PortLookupError("serial_number is empty; set it in the config file")

    ports = list_serial_ports() if ports is None else ports
    wanted = serial_number.strip().lower()
    matches = [p for p in ports if p.serial_number and p.serial_number.strip().lower() == wanted]

    if len(matches) > 1:
        devices = ", ".join(p.device for p in matches)
        raise PortLookupError(
            f"serial number {serial_number!r} matches more than one port ({devices}); "
            "cannot tell the arms apart"
        )
    if not matches:
        raise PortLookupError(
            f"no serial port with serial number {serial_number!r}. Ports found:\n"
            f"{describe_ports(ports)}"
        )
    return matches[0].device


def resolve_port_spec(
    serial_number: str | None,
    port: str | None,
    ports: list[PortInfo] | None = None,
) -> str:
    """설정이 준 방식대로 COM 포트를 정한다.

    ArmConfig 를 받지 않고 필드 2개를 직접 받는다. 그래야 이 모듈이 config.py 를
    import 하지 않고 독립적으로 테스트된다.

    Raises:
        PortLookupError: 둘 다 주거나 둘 다 안 줬을 때, 또는 조회에 실패했을 때.
    """
    if (serial_number is None) == (port is None):
        raise PortLookupError(
            "specify exactly one of serial_number or port "
            f"(got serial_number={serial_number!r}, port={port!r})"
        )
    if port is not None:
        return port
    assert serial_number is not None
    return find_port_by_serial(serial_number, ports)
