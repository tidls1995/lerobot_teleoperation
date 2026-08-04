"""장시간 소킹 - 오래 켜두고 무너지는지 본다.

짧게 돌려서는 **원리상 나타날 수 없는** 고장이 있다. 조금씩 새는 메모리, 넘치는
카운터, 하루에 한 번 튀는 무선 지연 같은 것들이다. 1단계 통과 기준에 24시간 연속
실행이 들어 있는 이유이고, 세 번 미뤄진 항목이다 (스펙 §11).

가장 알고 싶은 것은 **RTT 의 꼬리**다. 평균 15ms 는 이미 안다. 문제는 하루에 한 번
200ms(서버 워치독)를 넘느냐이고, 그건 하루를 돌려야만 답이 나온다.

    python -m tools.soak --config config/home.yaml --hours 24

**클러치를 절대 잡지 않는다.** 무인 방치이므로 팔이 움직여서는 안 된다. 서버는
ALIGNING 에 머물고 팔로워 토크는 꺼진 채다. 그 대가로 **모터에 쓰는 경로
(sync_write) 는 검증되지 않는다** - 읽기, 네트워크, 영상, 메모리만 본다. 쓰기는
사람이 지켜보는 짧은 소킹으로 따로 확인해야 한다.

**표본을 전부 저장하지 않는다.** 24시간이면 RTT 표본이 500만 개가 넘어 리스트에
쌓으면 수십 MB 가 된다. 메모리 누수를 재는 도구가 스스로 메모리를 먹으면 측정이
무의미해지므로, 히스토그램 통에 세고 최악값만 따로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path

from common.config import load_home_config
from common.protocol import State

log = logging.getLogger(__name__)

#: RTT 히스토그램 경계(ms). 촘촘한 쪽은 정상 구간(15ms 부근), 성긴 쪽은 사고 구간이다.
#: 200 은 서버 워치독이고, 그 위로 간 표본은 곧 HOLD 를 뜻한다.
RTT_EDGES = [5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 300, 500, 1000]

#: 최악 표본을 몇 개까지 시각과 함께 남길지. 언제 튀었는지 알아야 원인을 좁힌다.
WORST_KEPT = 20

#: 링크를 얼마나 자주 들여다볼지. 텔레메트리(60Hz)보다 빨라야 표본을 놓치지 않는다.
POLL_HZ = 200.0

#: 메모리 기준선을 잡기 전에 기다릴 시간(초).
#:
#: 시작 직후에는 버퍼와 캐시가 자리를 잡느라 메모리가 반드시 늘어난다. 그 구간을
#: 기준으로 삼으면 **정상인 것을 누수라고 부르게 된다** - 실측으로 2분 실행이
#: "하루 +429MB" 로 보고됐다. 안정된 뒤부터 재야 증가가 진짜 증가다.
MEMORY_WARMUP_S = 300.0


class Histogram:
    """경계로 나뉜 통에 세기만 한다. 표본 개수와 무관하게 메모리가 일정하다."""

    def __init__(self, edges: list[float]) -> None:
        self._edges = list(edges)
        self._counts = [0] * (len(edges) + 1)
        self.count = 0
        self.total = 0.0
        self.max: float | None = None
        self.min: float | None = None

    def add(self, value: float) -> None:
        self._counts[bisect_left(self._edges, value)] += 1
        self.count += 1
        self.total += value
        if self.max is None or value > self.max:
            self.max = value
        if self.min is None or value < self.min:
            self.min = value

    @property
    def mean(self) -> float | None:
        return None if self.count == 0 else self.total / self.count

    def above(self, threshold: float) -> int:
        """threshold 를 **확실히** 넘은 표본 수.

        통에 세기만 하므로 경계에 걸친 통(예: 150~200 통)은 넘은 표본과 안 넘은
        표본이 섞여 있다. 그런 통은 **세지 않는다** - 안 넘은 것을 넘었다고 보고하는
        쪽이 더 나쁘기 때문이다. 안전 보고서의 거짓 경보는 진짜 경보까지 무시하게
        만든다. 대신 정확한 최댓값(`max`)을 함께 보면 넘었는지 여부는 확실히 안다.
        """
        # 통 i 의 하한은 edges[i-1] 이다. 하한이 threshold 이상인 통부터 센다.
        first = bisect_left(self._edges, threshold) + 1
        return sum(self._counts[first:])

    def quantile(self, q: float) -> str:
        """분위수를 **통 단위**로 돌려준다.

        통에 세기만 하므로 정확한 값은 알 수 없다. '20ms 이하'처럼 경계로 답하는
        것이 정직하다 - 통 안에서 보간하면 없는 정밀도를 지어내는 것이 된다.
        """
        if self.count == 0:
            return "n/a"
        target = q * self.count
        seen = 0
        for i, n in enumerate(self._counts):
            seen += n
            if seen >= target:
                if i == 0:
                    return f"<={self._edges[0]:g}ms"
                if i == len(self._edges):
                    return f">{self._edges[-1]:g}ms"
                return f"<={self._edges[i]:g}ms"
        return "n/a"

    def rows(self) -> list[tuple[str, int]]:
        out = []
        low = "0"
        for i, edge in enumerate(self._edges):
            out.append((f"{low}-{edge:g}", self._counts[i]))
            low = f"{edge:g}"
        out.append((f">{self._edges[-1]:g}", self._counts[-1]))
        return [r for r in out if r[1] > 0]


def _rss_mb() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


class SoakRecorder:
    """표본을 받아 요약 가능한 형태로만 들고 있는다. 네트워크도 시계도 모른다."""

    def __init__(
        self, worst_kept: int = WORST_KEPT, memory_warmup_s: float = MEMORY_WARMUP_S
    ) -> None:
        self.rtt = Histogram(RTT_EDGES)
        self.worst: list[tuple[float, float]] = []  # (rtt_ms, uptime_s)
        self.transitions: list[tuple[float, str, str]] = []  # (uptime_s, state, reason)
        self.telemetry_seen = 0
        self.lost = 0
        self.rss_base: float | None = None
        self.rss_base_at: float | None = None
        self.rss_max: float | None = None
        self.rss_last: float | None = None
        self.video_frames: dict[int, int] = {}
        self._worst_kept = worst_kept
        self._memory_warmup_s = memory_warmup_s
        self._last_state: str | None = None

    def add_rtt(self, rtt_ms: float, uptime_s: float) -> None:
        self.rtt.add(rtt_ms)
        self.worst.append((rtt_ms, uptime_s))
        if len(self.worst) > self._worst_kept:
            self.worst.sort(key=lambda p: p[0], reverse=True)
            del self.worst[self._worst_kept :]

    def add_state(self, state: str, reason: str, uptime_s: float) -> None:
        """상태가 **바뀔 때만** 남긴다. 분 단위 표본으로는 짧은 HOLD 를 놓친다."""
        if state == self._last_state:
            return
        self._last_state = state
        self.transitions.append((uptime_s, state, reason))

    def add_rss(self, mb: float | None, uptime_s: float) -> None:
        """기준선은 워밍업이 끝난 뒤 처음 들어온 값으로 잡는다."""
        if mb is None:
            return
        self.rss_last = mb
        if self.rss_max is None or mb > self.rss_max:
            self.rss_max = mb
        if self.rss_base is None and uptime_s >= self._memory_warmup_s:
            self.rss_base = mb
            self.rss_base_at = uptime_s

    def add_video(self, cam_id: int, frames: int) -> None:
        self.video_frames[cam_id] = self.video_frames.get(cam_id, 0) + frames

    def holds(self) -> list[tuple[float, str, str]]:
        return [t for t in self.transitions if t[1] in ("HOLD", "FAULT")]

    def sorted_worst(self) -> list[tuple[float, float]]:
        return sorted(self.worst, key=lambda p: p[0], reverse=True)


def _fmt_uptime(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


def summarize(rec: SoakRecorder, elapsed_s: float, watchdog_ms: float) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 66)
    add(f"soak summary - ran for {_fmt_uptime(elapsed_s)}")
    add("=" * 66)

    add("")
    add("round-trip time")
    if rec.rtt.count == 0:
        add("  no telemetry was ever received - the server was never reachable")
    else:
        add(f"  samples   {rec.rtt.count:,}")
        add(f"  mean      {rec.rtt.mean:.1f} ms")
        add(f"  min/max   {rec.rtt.min:.1f} / {rec.rtt.max:.1f} ms")
        add(f"  median    {rec.rtt.quantile(0.5)}")
        add(f"  p99       {rec.rtt.quantile(0.99)}")
        add(f"  p99.9     {rec.rtt.quantile(0.999)}")
        add("")
        add("  distribution")
        for label, n in rec.rtt.rows():
            share = 100.0 * n / rec.rtt.count
            bar = "#" * min(40, max(1, round(share / 2.5)))
            add(f"    {label:>10} ms  {n:>10,}  {share:5.2f}%  {bar}")
        add("")
        # 이것이 이 도구를 만든 이유다. 평균이 아니라 워치독을 넘은 횟수가 답이다.
        over = rec.rtt.above(watchdog_ms)
        if over:
            add(f"  *** {over:,} samples went past the {watchdog_ms:.0f} ms watchdog ***")
            add("      Each one is a moment the arm could have stopped mid-task.")
            add("      On Wi-Fi this is the expected failure; move to wired ethernet.")
        elif rec.rtt.max >= watchdog_ms:
            add(f"  worst sample {rec.rtt.max:.1f} ms - at or past the watchdog")
        else:
            add(f"  never came within reach of the {watchdog_ms:.0f} ms watchdog")

        add("")
        add("  worst samples (when they happened)")
        for rtt_ms, at in rec.sorted_worst()[:10]:
            add(f"    {rtt_ms:8.1f} ms  at {_fmt_uptime(at)}")

    add("")
    add("state changes")
    holds = rec.holds()
    if not rec.transitions:
        add("  none recorded")
    else:
        for at, state, reason in rec.transitions:
            add(f"  {_fmt_uptime(at):>10}  {state:<12} {reason}")
    add("")
    if holds:
        add(f"  *** {len(holds)} unexpected HOLD/FAULT ***")
        add("      Nobody touched the arm, so every one of these is a real defect.")
    else:
        add("  no HOLD or FAULT - this is the result we wanted")

    add("")
    add("memory (this client)")
    if rec.rss_last is None:
        add("  not measured (psutil missing)")
    elif rec.rss_base is None:
        # 시작 직후에는 버퍼와 캐시가 자리를 잡느라 메모리가 반드시 늘어난다.
        # 그 구간에서 하루치를 외삽하면 정상인 것을 누수라고 부르게 된다.
        add(f"  peak {rec.rss_max:.1f} MB, now {rec.rss_last:.1f} MB")
        add(f"  too short to judge growth - the baseline is taken after ")
        add(f"  {MEMORY_WARMUP_S / 60:.0f} minutes, once startup allocation has settled")
    else:
        span = max(elapsed_s - rec.rss_base_at, 1.0)
        grew = rec.rss_last - rec.rss_base
        per_day = grew / span * 86400.0
        add(f"  baseline at {_fmt_uptime(rec.rss_base_at)}  {rec.rss_base:.1f} MB")
        add(f"  end / peak                {rec.rss_last:.1f} / {rec.rss_max:.1f} MB")
        add(f"  growth since baseline     {grew:+.1f} MB over {_fmt_uptime(span)}")
        add(f"                            ({per_day:+.1f} MB/day at this rate)")
        if per_day > 50.0:
            add("  *** growing. Something is not being released; find it before running")
            add("      unattended for weeks. ***")

    add("")
    add("packets and video")
    add(f"  telemetry received  {rec.telemetry_seen:,}")
    add(f"  telemetry lost      {rec.lost:,}")
    if rec.telemetry_seen:
        total = rec.telemetry_seen + rec.lost
        add(f"  loss rate           {100.0 * rec.lost / total:.4f}%")
    if rec.video_frames:
        for cam_id in sorted(rec.video_frames):
            n = rec.video_frames[cam_id]
            add(f"  cam {cam_id} frames        {n:,}  ({n / max(elapsed_s, 1.0):.1f} fps average)")
    else:
        add("  no video frames received")

    add("")
    add("=" * 66)
    return "\n".join(lines)


def run_soak(config_path: str, hours: float, out_dir: str, cameras: int) -> int:
    from home.client import CommandState, ControlLink, LeaderSender, build_leader

    cfg = load_home_config(config_path)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jsonl_path = out / f"soak-{stamp}.jsonl"
    summary_path = out / f"soak-{stamp}-summary.txt"
    log_path = out / f"soak-{stamp}.log"

    # 로그를 파일로도 남긴다. 밤새 돌리면 터미널 스크롤백은 남지 않는다.
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    rec = SoakRecorder()
    link = ControlLink(host=cfg.server_host, port=cfg.control_port)
    commands = CommandState()
    commands.set_clutch(False)  # 명시. 무인이므로 팔은 끝까지 움직이지 않는다.

    leader = build_leader(cfg)
    # connect() 는 Arm 규약이 아니라 실물 어댑터에만 있다 (mock 은 열 장치가 없다).
    connect = getattr(leader, "connect", None)
    if callable(connect):
        connect()
    link.start()
    sender = LeaderSender(link=link, leader=leader, commands=commands)
    sender.start()

    video = None
    if cameras > 0:
        from home.video_recv import VideoClient

        video = VideoClient(host=cfg.server_host, port=cfg.video_port)
        video.start()

    print(f"soaking for {hours:g} h - the clutch is never engaged, the arms will not move")
    print(f"  log     {log_path}")
    print(f"  samples {jsonl_path}")
    print("Ctrl-C stops early and still writes the summary.")
    print()

    started = time.monotonic()
    deadline = started + hours * 3600.0
    interval = 1.0 / POLL_HZ
    last_recv_at: float | None = None
    last_row_at = started
    last_video_seq: dict[int, int] = {}
    window_max_rtt = 0.0
    window_samples = 0

    try:
        with jsonl_path.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                now = time.monotonic()
                uptime = now - started

                snap = link.snapshot()
                if snap is not None:
                    packet, recv_at, rtt_ms, lost = snap
                    # 수신 시각으로 중복을 거른다. 받은 패킷마다 정확히 한 번씩 센다.
                    if recv_at != last_recv_at:
                        last_recv_at = recv_at
                        rec.telemetry_seen += 1
                        rec.lost = lost
                        state_name = State(packet.state).name
                        rec.add_state(state_name, f"flags={packet.flags}", uptime)
                        if rtt_ms is not None:
                            rec.add_rtt(rtt_ms, uptime)
                            window_max_rtt = max(window_max_rtt, rtt_ms)
                            window_samples += 1

                if video is not None:
                    for cam_id in range(cameras):
                        latest = video.latest(cam_id)
                        if latest is None:
                            continue
                        _, _, seq = latest
                        previous = last_video_seq.get(cam_id)
                        if previous is not None and seq > previous:
                            rec.add_video(cam_id, seq - previous)
                        last_video_seq[cam_id] = seq

                # 1분에 한 줄. 하루면 1440줄이라 나중에 그래프로 그리기 좋고,
                # 파일이 디스크를 채우지도 않는다.
                if now - last_row_at >= 60.0:
                    rss = _rss_mb()
                    rec.add_rss(rss, uptime)
                    row = {
                        "uptime_s": round(uptime, 1),
                        "wall": datetime.now().isoformat(timespec="seconds"),
                        "rtt_max_ms": round(window_max_rtt, 2) if window_samples else None,
                        "rtt_samples": window_samples,
                        "send_hz": round(sender.send_hz, 1),
                        "telemetry": rec.telemetry_seen,
                        "lost": rec.lost,
                        "state": rec._last_state,
                        "rss_mb": None if rss is None else round(rss, 1),
                        "video": dict(rec.video_frames),
                    }
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()  # 도중에 죽어도 여기까지는 남는다
                    print(
                        f"  {_fmt_uptime(uptime)}  rtt_max {row['rtt_max_ms']} ms  "
                        f"send {row['send_hz']} Hz  state {row['state']}  "
                        f"rss {row['rss_mb']} MB",
                        flush=True,
                    )
                    last_row_at = now
                    window_max_rtt = 0.0
                    window_samples = 0

                time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped early")
    finally:
        sender.stop()
        link.stop()
        if video is not None:
            video.stop()
        leader.close()

    elapsed = time.monotonic() - started
    rec.add_rss(_rss_mb(), elapsed)
    text = summarize(rec, elapsed, watchdog_ms=200.0)
    summary_path.write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"written to {summary_path}")

    # 종료 코드로 판정한다. 자동으로 돌릴 때 사람이 요약을 읽지 않아도 알 수 있다.
    if rec.rtt.count == 0 or rec.holds():
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the teleoperation link for hours and record it")
    parser.add_argument("--config", default="config/home.yaml")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--out", default="soak_runs")
    parser.add_argument("--cameras", type=int, default=2, help="how many camera ids to follow")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")
    return run_soak(args.config, args.hours, args.out, args.cameras)


if __name__ == "__main__":
    raise SystemExit(main())
