import pygame
import pytest

from home.hud import ClutchTracker, HudInput


def test_clutch_is_false_initially():
    assert ClutchTracker().poll(now=0.0).clutch is False


def test_clutch_is_true_while_space_held():
    t = ClutchTracker()
    t.on_key_down(pygame.K_SPACE, now=0.0)
    assert t.poll(now=0.1).clutch is True
    assert t.poll(now=0.5).clutch is True


def test_clutch_clears_on_key_up():
    t = ClutchTracker()
    t.on_key_down(pygame.K_SPACE, now=0.0)
    t.on_key_up(pygame.K_SPACE, now=0.2)
    assert t.poll(now=0.3).clutch is False


def test_reset_requires_holding_r_for_three_seconds():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=1.0).reset is False
    assert t.poll(now=2.9).reset is False
    assert t.poll(now=3.1).reset is True


def test_reset_fires_only_once_per_hold():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=3.1).reset is True
    assert t.poll(now=3.2).reset is False
    assert t.poll(now=5.0).reset is False


def test_reset_can_fire_again_after_releasing_and_re_holding():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    assert t.poll(now=3.1).reset is True
    t.on_key_up(pygame.K_r, now=3.5)
    t.on_key_down(pygame.K_r, now=4.0)
    assert t.poll(now=7.1).reset is True


def test_releasing_r_early_cancels_reset():
    t = ClutchTracker(reset_hold_s=3.0)
    t.on_key_down(pygame.K_r, now=0.0)
    t.on_key_up(pygame.K_r, now=1.0)
    assert t.poll(now=4.0).reset is False


def test_quit_is_reported_once():
    t = ClutchTracker()
    t.on_key_down(pygame.K_ESCAPE, now=0.0)
    assert t.poll(now=0.1).quit is True
    assert t.poll(now=0.2).quit is False


def test_toggle_motion_is_reported_once_per_press():
    t = ClutchTracker()
    t.on_key_down(pygame.K_m, now=0.0)
    assert t.poll(now=0.1).toggle_motion is True
    assert t.poll(now=0.2).toggle_motion is False
    t.on_key_up(pygame.K_m, now=0.3)
    t.on_key_down(pygame.K_m, now=0.4)
    assert t.poll(now=0.5).toggle_motion is True


def test_unrelated_keys_do_nothing():
    t = ClutchTracker()
    t.on_key_down(pygame.K_z, now=0.0)
    assert t.poll(now=0.1) == HudInput(clutch=False, reset=False, quit=False, toggle_motion=False)


# --- 혼자 테스트할 때 쓰는 토글 클러치 --------------------------------------
#
# 기본은 hold 다. 놓으면 즉시 멈추는 성질을 잃기 때문에 토글은 명시적으로
# 켜야만 쓸 수 있고, 화면에 항상 표시된다.


def test_hold_is_the_default_mode():
    t = ClutchTracker()
    assert t.mode == "hold"


def test_toggle_mode_latches_on_press():
    t = ClutchTracker(mode="toggle")
    t.on_key_down(pygame.K_SPACE, now=0.0)
    assert t.poll(now=0.1).clutch is True
    # 손을 떼도 유지된다 - 이것이 hold 와의 차이다
    t.on_key_up(pygame.K_SPACE, now=0.2)
    assert t.poll(now=0.3).clutch is True


def test_toggle_mode_releases_on_second_press():
    t = ClutchTracker(mode="toggle")
    t.on_key_down(pygame.K_SPACE, now=0.0)
    t.on_key_up(pygame.K_SPACE, now=0.1)
    assert t.poll(now=0.2).clutch is True
    t.on_key_down(pygame.K_SPACE, now=0.3)
    assert t.poll(now=0.4).clutch is False


def test_toggle_mode_produces_a_rising_edge_each_time_it_latches():
    """게이트는 클러치의 상승 에지를 요구한다. 토글도 그것을 만들어야 한다."""
    t = ClutchTracker(mode="toggle")
    assert t.poll(now=0.0).clutch is False
    t.on_key_down(pygame.K_SPACE, now=0.1)
    assert t.poll(now=0.2).clutch is True  # False -> True 전이
    t.on_key_down(pygame.K_SPACE, now=0.3)
    assert t.poll(now=0.4).clutch is False
    t.on_key_down(pygame.K_SPACE, now=0.5)
    assert t.poll(now=0.6).clutch is True  # 다시 False -> True


def test_hold_mode_is_unaffected_by_key_repeat():
    """hold 모드에서 같은 키가 여러 번 눌려도 켜진 상태가 유지되어야 한다."""
    t = ClutchTracker(mode="hold")
    t.on_key_down(pygame.K_SPACE, now=0.0)
    t.on_key_down(pygame.K_SPACE, now=0.1)
    assert t.poll(now=0.2).clutch is True
    t.on_key_up(pygame.K_SPACE, now=0.3)
    assert t.poll(now=0.4).clutch is False


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        ClutchTracker(mode="latch")
