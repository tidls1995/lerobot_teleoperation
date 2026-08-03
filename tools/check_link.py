"""조종을 시작하기 전에 두 PC 사이의 링크만 따로 확인한다.

두 PC 로 나누면 새로운 실패 원인이 넷 생긴다: 방화벽, 잘못된 IP, 서버 미기동,
프로토콜 버전 어긋남. 전체 시스템을 띄워 놓고 찾으면 팔이 통전된 채로 디버깅하게
된다. 이 도구는 팔을 만지지 않고 링크만 본다.

제어 확인은 진짜 제어 패킷을 한 장 보낸다. clutch=0, cmd=NONE, 관절값 0 이므로
서버는 ALIGNING 으로 가며 현재 자세를 유지할 뿐 **움직이지 않는다**.

    python -m tools.check_link --host 192.168.0.42
    python -m tools.check_link --config config/home.yaml
"""

from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass

from common.netutil import recv_exactly
from common.protocol import (
    N_JOINTS,
    TELEMETRY_SIZE,
    VIDEO_HEADER_SIZE,
    Cmd,
    ControlPacket,
    TelemetryPacket,
    VideoHeader,
)


@dataclass(frozen=True)
class LinkResult:
    ok: bool
    detail: str


def check_control(host: str, port: int, timeout: float = 3.0) -> LinkResult:
    """제어 패킷 한 장을 보내고 텔레메트리가 돌아오는지 본다."""
    packet = ControlPacket(
        seq=1,
        t_send=time.time(),
        clutch=False,
        cmd=Cmd.NONE,
        joints=tuple([0.0] * N_JOINTS),
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        started = time.monotonic()
        sock.sendto(packet.pack(), (host, port))
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            return LinkResult(
                False,
                f"no reply from {host}:{port}/udp within {timeout:.1f}s. "
                "Is the server running? Is the Windows firewall allowing UDP 5555?",
            )
        except ConnectionResetError:
            # Windows 는 닫힌 UDP 포트에 대해 ICMP port-unreachable 을 받으면 다음
            # recv 에서 WSAECONNRESET 을 던진다. 타임아웃이 아니라 '거절'이므로
            # 오히려 정보가 더 많다 - 방화벽이 아니라 서버가 안 떠 있는 것이다.
            return LinkResult(
                False,
                f"no reply from {host}:{port}/udp - the port is closed. "
                "The server is not running on that PC (a firewall block would time out instead).",
            )
        except OSError as exc:
            return LinkResult(False, f"send failed to {host}:{port}/udp: {exc}")
        rtt_ms = (time.monotonic() - started) * 1000.0

        if len(data) != TELEMETRY_SIZE:
            return LinkResult(False, f"reply was {len(data)} bytes, expected {TELEMETRY_SIZE}")
        telemetry = TelemetryPacket.unpack(data)
        if telemetry is None:
            return LinkResult(
                False,
                "reply did not parse - the two sides are running different protocol "
                "versions. Pull the same commit on both PCs.",
            )
        return LinkResult(
            True, f"control ok, RTT {rtt_ms:.1f} ms, server state {telemetry.state.name}"
        )
    finally:
        sock.close()


def check_video(host: str, port: int, timeout: float = 5.0) -> LinkResult:
    """영상 TCP 에 붙어 프레임 한 장을 받아본다."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return LinkResult(
            False,
            f"cannot connect to {host}:{port}/tcp: {exc}. "
            "Is the server running? Is the Windows firewall allowing TCP 5556?",
        )
    try:
        sock.settimeout(timeout)
        header_bytes = recv_exactly(sock, VIDEO_HEADER_SIZE)
        if header_bytes is None:
            return LinkResult(False, "connected but the server closed without sending a frame")
        header = VideoHeader.unpack(header_bytes)
        if header is None:
            return LinkResult(
                False,
                "frame header did not parse - different protocol versions on the two PCs",
            )
        payload = recv_exactly(sock, header.length)
        if payload is None:
            return LinkResult(False, "frame header arrived but the image did not")
        return LinkResult(
            True, f"video ok, first frame from cam {header.cam_id}, {header.length} bytes"
        )
    except socket.timeout:
        return LinkResult(
            False,
            f"connected to {host}:{port}/tcp but no frame within {timeout:.1f}s. "
            "Are any cameras configured and working on the server?",
        )
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the link to the workbench PC")
    parser.add_argument("--host", help="workbench PC address; overrides the config")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--control-port", type=int)
    parser.add_argument("--video-port", type=int)
    parser.add_argument("--skip-video", action="store_true", help="only check the control link")
    args = parser.parse_args(argv)

    from common.config import load_home_config

    cfg = load_home_config(args.config)
    host = args.host or cfg.server_host
    control_port = args.control_port or cfg.control_port
    video_port = args.video_port or cfg.video_port

    print(f"checking {host} (control udp/{control_port}, video tcp/{video_port})")
    results = [("control", check_control(host, control_port))]
    if not args.skip_video:
        results.append(("video", check_video(host, video_port)))

    failed = False
    for name, result in results:
        mark = "OK  " if result.ok else "FAIL"
        print(f"  [{mark}] {name}: {result.detail}")
        failed = failed or not result.ok

    if failed:
        print()
        print("Nothing was moved. Fix the link before starting the client.")
        return 1
    print()
    print("Link is good. You can start the client.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
