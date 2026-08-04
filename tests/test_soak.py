"""소킹 도구의 판정 로직. 24시간을 기다리지 않고 검증할 수 있어야 한다.

이 도구가 틀리면 "하루 돌려봤는데 괜찮더라"라는 **잘못된 안심**을 준다. 그것이
소킹을 아예 안 한 것보다 나쁘다. 그래서 집계·판정은 네트워크와 분리해 여기서 검증한다.
"""

import pytest

from tools.soak import RTT_EDGES, Histogram, SoakRecorder, summarize


# --- 히스토그램: 표본이 아무리 많아도 메모리가 늘지 않아야 한다 -----------------


def test_memory_does_not_grow_with_sample_count():
    """메모리 누수를 재는 도구가 스스로 메모리를 먹으면 측정이 무의미해진다."""
    h = Histogram(RTT_EDGES)
    before = len(h._counts)
    for i in range(100_000):
        h.add(i % 250)
    assert len(h._counts) == before
    assert h.count == 100_000


def test_mean_min_max_are_exact():
    """통으로 세어도 이 셋은 정확해야 한다. 최댓값이 판정의 근거이기 때문이다."""
    h = Histogram(RTT_EDGES)
    for v in (10.0, 20.0, 300.0):
        h.add(v)
    assert h.min == 10.0
    assert h.max == 300.0
    assert h.mean == pytest.approx(110.0)


def test_empty_histogram_is_safe():
    h = Histogram(RTT_EDGES)
    assert h.count == 0
    assert h.mean is None
    assert h.max is None
    assert h.quantile(0.5) == "n/a"
    assert h.above(200) == 0
    assert h.rows() == []


# --- 워치독 초과 판정: 거짓 경보를 내지 않는 쪽으로 -----------------------------
#
# 통 경계에 걸친 통(150~200)에는 넘은 표본과 안 넘은 표본이 섞여 있다. 그런 통은
# 세지 않는다. 안전 보고서에서 거짓 경보는 진짜 경보까지 무시하게 만든다.


def test_a_sample_below_the_watchdog_is_not_reported_as_over():
    h = Histogram(RTT_EDGES)
    h.add(160.0)  # 150~200 통. 200 을 넘지 않았다.
    assert h.above(200) == 0, "안 넘은 표본을 넘었다고 보고하면 안 된다"


def test_samples_past_the_watchdog_are_counted():
    h = Histogram(RTT_EDGES)
    h.add(250.0)
    h.add(600.0)
    h.add(1500.0)
    assert h.above(200) == 3


def test_the_exact_max_still_reveals_a_borderline_spike():
    """통으로는 못 세도 최댓값으로는 알 수 있어야 한다."""
    h = Histogram(RTT_EDGES)
    h.add(200.0)
    assert h.above(200) == 0
    assert h.max == 200.0


def test_quantiles_answer_in_bucket_edges_not_invented_precision():
    h = Histogram(RTT_EDGES)
    for _ in range(99):
        h.add(12.0)
    h.add(400.0)
    assert h.quantile(0.5) == "<=15ms"
    assert h.quantile(0.999) == "<=500ms"


# --- 기록기 --------------------------------------------------------------------


def test_only_the_worst_samples_are_kept_with_their_timestamps():
    rec = SoakRecorder(worst_kept=3)
    for i in range(100):
        rec.add_rtt(float(i), uptime_s=float(i) * 10)
    worst = rec.sorted_worst()
    assert len(worst) == 3
    assert [w[0] for w in worst] == [99.0, 98.0, 97.0]
    assert worst[0][1] == 990.0, "언제 튀었는지가 원인 추적의 단서다"
    assert rec.rtt.count == 100, "버린 것도 히스토그램에는 남아야 한다"


def test_a_state_is_recorded_only_when_it_changes():
    rec = SoakRecorder()
    for i in range(1000):
        rec.add_state("ALIGNING", "", float(i))
    assert len(rec.transitions) == 1


def test_a_brief_hold_is_not_missed():
    """분 단위 표본으로는 짧은 HOLD 를 놓친다. 그래서 바뀔 때마다 기록한다."""
    rec = SoakRecorder()
    rec.add_state("ALIGNING", "", 0.0)
    rec.add_state("HOLD", "flags=2", 3600.0)
    rec.add_state("ALIGNING", "", 3600.5)
    holds = rec.holds()
    assert len(holds) == 1
    assert holds[0][0] == 3600.0


def test_memory_high_water_mark_is_kept():
    rec = SoakRecorder(memory_warmup_s=0.0)
    for i, mb in enumerate((100.0, 250.0, 120.0)):
        rec.add_rss(mb, uptime_s=float(i))
    assert rec.rss_base == 100.0
    assert rec.rss_max == 250.0
    assert rec.rss_last == 120.0


def test_missing_psutil_does_not_break_recording():
    rec = SoakRecorder()
    rec.add_rss(None, uptime_s=999.0)
    assert rec.rss_base is None
    assert rec.rss_last is None


# --- 메모리 기준선은 워밍업 뒤에 잡는다 ------------------------------------------
#
# 시작 직후에는 버퍼와 캐시가 자리를 잡느라 메모리가 반드시 늘어난다. 그 구간을
# 기준으로 삼으면 정상인 것을 누수라고 부르게 된다. 실측: 2분 실행이 "하루 +429MB"
# 로 보고됐다. 안전 보고서의 거짓 경보는 진짜 경보까지 무시하게 만든다.


def test_the_startup_spike_is_not_used_as_the_baseline():
    rec = SoakRecorder(memory_warmup_s=300.0)
    rec.add_rss(40.0, uptime_s=60.0)  # 기동 중
    rec.add_rss(41.5, uptime_s=120.0)  # 아직 기동 중
    rec.add_rss(42.0, uptime_s=300.0)  # 안정됨 -> 여기가 기준선
    rec.add_rss(42.1, uptime_s=600.0)
    assert rec.rss_base == 42.0
    assert rec.rss_base_at == 300.0


def test_a_short_run_refuses_to_judge_memory_growth():
    rec = SoakRecorder()
    for _ in range(1000):
        rec.add_rtt(15.0, uptime_s=1.0)
    rec.add_state("ALIGNING", "", 0.0)
    rec.telemetry_seen = 1000
    rec.add_rss(41.5, uptime_s=60.0)
    rec.add_rss(42.1, uptime_s=120.0)
    assert rec.rss_base is None, "워밍업 전에는 기준선이 없어야 한다"
    text = summarize(rec, elapsed_s=129.0, watchdog_ms=200.0)
    assert "too short to judge growth" in text
    assert "***" not in text, "짧은 실행을 누수라고 소리치면 안 된다"


# --- 요약: 나쁜 결과를 조용히 넘기지 않는다 -------------------------------------


def make_clean_recorder():
    rec = SoakRecorder()
    for _ in range(1000):
        rec.add_rtt(15.0, uptime_s=1.0)
    rec.add_state("ALIGNING", "", 0.0)
    rec.add_rss(100.0, uptime_s=300.0)
    rec.add_rss(101.0, uptime_s=86400.0)
    rec.telemetry_seen = 1000
    rec.add_video(0, 500)
    return rec


def test_a_clean_run_says_so():
    text = summarize(make_clean_recorder(), elapsed_s=86400.0, watchdog_ms=200.0)
    assert "no HOLD or FAULT" in text
    assert "***" not in text


def test_a_hold_is_shouted_about():
    """아무도 팔을 건드리지 않았으므로 HOLD 하나하나가 진짜 결함이다."""
    rec = make_clean_recorder()
    rec.add_state("HOLD", "flags=2", 40000.0)
    text = summarize(rec, elapsed_s=86400.0, watchdog_ms=200.0)
    assert "1 unexpected HOLD/FAULT" in text
    assert "***" in text


def test_a_watchdog_breach_is_shouted_about():
    rec = make_clean_recorder()
    rec.add_rtt(450.0, uptime_s=50000.0)
    text = summarize(rec, elapsed_s=86400.0, watchdog_ms=200.0)
    assert "past the 200 ms watchdog" in text
    assert "***" in text
    assert "13:53:20" in text, "언제 튀었는지가 보여야 한다"


def test_memory_growth_is_shouted_about():
    rec = make_clean_recorder()
    rec.add_rss(400.0, uptime_s=86400.0)  # 기준선(100MB) 대비 300MB 증가
    text = summarize(rec, elapsed_s=86400.0, watchdog_ms=200.0)
    assert "growing" in text
    assert "+300.0 MB" in text


def test_a_run_that_never_reached_the_server_is_not_reported_as_success():
    """텔레메트리가 0인데 'HOLD 없음'으로 통과시키면 최악의 거짓 안심이 된다."""
    rec = SoakRecorder()
    text = summarize(rec, elapsed_s=86400.0, watchdog_ms=200.0)
    assert "never reachable" in text


def test_summary_does_not_crash_without_video():
    rec = SoakRecorder()
    for _ in range(10):
        rec.add_rtt(12.0, uptime_s=1.0)
    rec.telemetry_seen = 10
    text = summarize(rec, elapsed_s=60.0, watchdog_ms=200.0)
    assert "no video frames received" in text
