"""소켓 헬퍼."""

from __future__ import annotations


def recv_exactly(sock, n: int) -> bytes | None:
    """소켓에서 정확히 n바이트를 모아 반환한다.

    TCP 의 ``recv(n)`` 은 요청한 것보다 적게 돌려줄 수 있다. 한 번만 호출하고
    다 받았다고 믿는 것이 TCP 프로그래밍에서 가장 흔한 버그다.

    Returns:
        n바이트. 도중에 연결이 끊기면 None.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return b""

    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
