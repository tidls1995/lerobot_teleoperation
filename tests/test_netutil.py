import pytest

from common.netutil import recv_exactly


class ChunkedSocket:
    """recv 가 요청보다 적게 돌려주는 상황을 재현하는 가짜 소켓."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, n):
        if not self._chunks:
            return b""  # 연결 종료
        chunk = self._chunks.pop(0)
        return chunk[:n]


def test_reassembles_data_split_across_many_recv_calls():
    sock = ChunkedSocket([b"ab", b"cd", b"ef"])
    assert recv_exactly(sock, 6) == b"abcdef"


def test_returns_none_when_connection_closes_early():
    sock = ChunkedSocket([b"ab"])
    assert recv_exactly(sock, 6) is None


def test_single_chunk():
    assert recv_exactly(ChunkedSocket([b"hello"]), 5) == b"hello"


def test_zero_length_returns_empty_bytes():
    assert recv_exactly(ChunkedSocket([]), 0) == b""


def test_does_not_over_read():
    sock = ChunkedSocket([b"abcdefghij"])
    assert recv_exactly(sock, 4) == b"abcd"


def test_negative_length_is_rejected():
    with pytest.raises(ValueError):
        recv_exactly(ChunkedSocket([]), -1)
