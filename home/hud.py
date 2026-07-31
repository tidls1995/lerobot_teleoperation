"""조종자 화면과 키 입력.

OpenCV 가 아니라 pygame 을 쓰는 이유가 두 가지 있다.

1. 이 환경의 opencv 는 headless 빌드라 imshow 가 아예 없다.
2. 더 근본적으로, cv2.waitKey 는 키를 **뗀** 것을 감지하지 못한다.
   클러치는 '누르고 있는 동안'이라는 의미이므로 KEYUP 이 필수다.

키 배치:
  SPACE (누르고 있기)  클러치 - 이걸 놓으면 팔로워가 즉시 그 자리에 정지
  R     (3초 길게)      HOLD 해제 요청
  M                     mock 리더의 움직임 토글 (1단계 데모용)
  ESC                   종료
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pygame

from common.protocol import JOINT_NAMES, N_JOINTS, Flag, State

# 색 (RGB)
_BG = (18, 18, 22)
_FG = (225, 225, 230)
_DIM = (120, 120, 130)
_GREEN = (60, 200, 110)
_AMBER = (230, 170, 60)
_RED = (225, 70, 70)
_GREY = (110, 110, 120)

_STATE_COLOR = {
    State.DISCONNECTED: _GREY,
    State.ALIGNING: _AMBER,
    State.ENGAGED: _GREEN,
    State.HOLD: _RED,
    State.FAULT: _RED,
}

_FLAG_LABELS = [
    (Flag.SPEED_CLAMPED, "SPEED"),
    (Flag.JOINT_LIMITED, "LIMIT"),
    (Flag.FOLLOW_ERROR, "FOLLOW"),
    (Flag.WATCHDOG, "WDOG"),
    (Flag.MOTOR_ERROR, "MOTOR"),
]


@dataclass(frozen=True)
class HudInput:
    clutch: bool
    reset: bool
    quit: bool
    toggle_motion: bool


@dataclass(frozen=True)
class HudStats:
    rtt_ms: float | None
    lost_packets: int
    video_connected: bool
    telemetry_age_ms: float | None
    #: 제어 패킷 실제 송신 레이트. 60Hz 아래로 떨어지면 워치독 위험 신호다.
    send_hz: float = 0.0


class ClutchTracker:
    """키 상태를 클러치·리셋·종료 신호로 바꾼다. pygame 창 없이 테스트 가능하다.

    Args:
        mode: ``"hold"`` (기본) 은 스페이스를 누르고 있는 동안만 engage 한다.
            ``"toggle"`` 은 한 번 누르면 걸리고 다시 누르면 풀린다.

    **토글은 기본값이 아니다.** hold 모드의 "놓으면 즉시 멈춘다"는 성질을 잃기
    때문이다. 집에서 리더를 놓으면 리더는 토크가 꺼져 있어 중력에 쓰러지는데,
    토글 상태라면 팔로워도 따라 쓰러진다 (스펙 §5.3).

    토글은 **혼자 시험할 때** 쓰기 위한 것이다. 양팔을 다 잡으면 스페이스를 누를
    손이 없다. 실제 작업에서는 풋페달을 쓰는 것이 맞다.
    """

    def __init__(self, reset_hold_s: float = 3.0, mode: str = "hold") -> None:
        if mode not in ("hold", "toggle"):
            raise ValueError(f"mode must be 'hold' or 'toggle', got {mode!r}")
        self.mode = mode
        self._reset_hold_s = reset_hold_s
        self._space_down = False
        self._r_down_at: float | None = None
        self._reset_fired = False
        self._quit_pending = False
        self._motion_pending = False

    @property
    def clutch(self) -> bool:
        return self._space_down

    def on_key_down(self, key: int, now: float) -> None:
        if key == pygame.K_SPACE:
            # 토글 모드에서는 누를 때마다 뒤집는다. hold 모드에서는 키 반복이
            # 들어와도 켜진 상태를 유지해야 하므로 그냥 True 로 둔다.
            self._space_down = (not self._space_down) if self.mode == "toggle" else True
        elif key == pygame.K_r:
            if self._r_down_at is None:
                self._r_down_at = now
                self._reset_fired = False
        elif key == pygame.K_ESCAPE:
            self._quit_pending = True
        elif key == pygame.K_m:
            self._motion_pending = True

    def on_key_up(self, key: int, now: float) -> None:
        if key == pygame.K_SPACE:
            # 토글 모드에서는 손을 떼도 걸린 상태가 유지된다.
            if self.mode == "hold":
                self._space_down = False
        elif key == pygame.K_r:
            self._r_down_at = None
            self._reset_fired = False

    def poll(self, now: float) -> HudInput:
        reset = False
        if self._r_down_at is not None and not self._reset_fired:
            if (now - self._r_down_at) >= self._reset_hold_s:
                reset = True
                self._reset_fired = True

        quit_now, self._quit_pending = self._quit_pending, False
        motion, self._motion_pending = self._motion_pending, False
        return HudInput(clutch=self._space_down, reset=reset, quit=quit_now, toggle_motion=motion)

    def reset_progress(self, now: float) -> float:
        """R 키를 얼마나 눌렀는지 0.0~1.0. 화면에 진행 막대를 그리는 용도."""
        if self._r_down_at is None or self._reset_fired:
            return 0.0
        return min(1.0, (now - self._r_down_at) / self._reset_hold_s)


class Hud:
    def __init__(
        self,
        cam_ids: list[int],
        cam_names: dict[int, str],
        width: int = 1000,
        height: int = 620,
        clutch_mode: str = "hold",
    ) -> None:
        pygame.init()
        pygame.display.set_caption("SO-101 Remote Teleoperation")
        self._screen = pygame.display.set_mode((width, height))
        self._font = pygame.font.SysFont("consolas", 15)
        self._big = pygame.font.SysFont("consolas", 26, bold=True)
        self._small = pygame.font.SysFont("consolas", 12)
        self._cam_ids = cam_ids
        self._cam_names = cam_names
        self._width = width
        self._height = height
        self._tracker = ClutchTracker(mode=clutch_mode)

    def poll(self, now: float) -> HudInput:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return HudInput(clutch=False, reset=False, quit=True, toggle_motion=False)
            if event.type == pygame.KEYDOWN:
                self._tracker.on_key_down(event.key, now)
            elif event.type == pygame.KEYUP:
                self._tracker.on_key_up(event.key, now)
        return self._tracker.poll(now)

    def draw(
        self,
        frames: dict[int, np.ndarray | None],
        telemetry,
        leader_joints: list[float] | None,
        stats: HudStats,
        align_threshold_deg: float,
        now: float = 0.0,
    ) -> None:
        self._screen.fill(_BG)

        state = telemetry.state if telemetry is not None else State.DISCONNECTED
        stale = stats.telemetry_age_ms is not None and stats.telemetry_age_ms > 300.0
        border = _RED if (stale or state in (State.HOLD, State.FAULT)) else _STATE_COLOR[state]

        self._draw_videos(frames)
        self._draw_joint_bars(telemetry, leader_joints, align_threshold_deg)
        self._draw_status(state, telemetry, stats, stale, now)

        pygame.draw.rect(self._screen, border, self._screen.get_rect(), width=6)
        pygame.display.flip()

    # ------------------------------------------------------------------ #

    def _draw_videos(self, frames: dict[int, np.ndarray | None]) -> None:
        pane_w, pane_h = 320, 240
        for i, cam_id in enumerate(self._cam_ids):
            x = 16 + i * (pane_w + 8)
            y = 16
            frame = frames.get(cam_id)
            if frame is None:
                pygame.draw.rect(self._screen, (35, 35, 40), (x, y, pane_w, pane_h))
                self._screen.blit(
                    self._font.render(f"cam {cam_id}: no signal", True, _DIM), (x + 10, y + 110)
                )
            else:
                # OpenCV 는 BGR, pygame 은 RGB. 축도 (h,w) -> (w,h) 로 뒤집는다.
                rgb = frame[:, :, ::-1]
                surface = pygame.surfarray.make_surface(np.transpose(rgb, (1, 0, 2)))
                surface = pygame.transform.scale(surface, (pane_w, pane_h))
                self._screen.blit(surface, (x, y))
            label = self._cam_names.get(cam_id, f"cam{cam_id}")
            self._screen.blit(self._small.render(label, True, _FG), (x + 4, y + pane_h + 3))

    def _draw_joint_bars(self, telemetry, leader_joints: list[float] | None, threshold: float) -> None:
        top = 312
        self._screen.blit(
            self._font.render("alignment error (leader vs follower)", True, _DIM), (16, top - 32)
        )
        if telemetry is None or leader_joints is None:
            self._screen.blit(self._font.render("waiting for telemetry...", True, _DIM), (16, top))
            return

        bar_w, bar_h, gap = 74, 16, 4
        for i in range(N_JOINTS):
            col, row = i % 6, i // 6
            x = 16 + col * (bar_w + 46)
            y = top + row * (bar_h + gap + 16)
            error = abs(leader_joints[i] - telemetry.joints[i])
            ok = error < threshold
            filled = int(min(1.0, error / max(threshold * 4.0, 1e-6)) * bar_w)

            pygame.draw.rect(self._screen, (45, 45, 52), (x, y, bar_w, bar_h))
            pygame.draw.rect(self._screen, _GREEN if ok else _AMBER, (x, y, filled or 2, bar_h))
            name = JOINT_NAMES[i].replace("left_", "L.").replace("right_", "R.")
            self._screen.blit(self._small.render(name, True, _DIM), (x, y - 13))
            self._screen.blit(self._small.render(f"{error:5.1f}", True, _FG), (x + bar_w + 4, y + 1))

    def _draw_status(self, state, telemetry, stats: HudStats, stale: bool, now: float) -> None:
        y = 470
        self._screen.blit(self._big.render(state.name, True, _STATE_COLOR[state]), (16, y))

        rtt = f"{stats.rtt_ms:5.1f} ms" if stats.rtt_ms is not None else "  --  "
        lines = [
            (f"RTT      {rtt}", _FG),
            (f"lost     {stats.lost_packets}", _FG),
            (f"video    {'connected' if stats.video_connected else 'DISCONNECTED'}",
             _FG if stats.video_connected else _RED),
            # 60Hz 아래로 떨어지면 화면 렉이 제어를 밀어내고 있다는 뜻이다.
            (f"send     {stats.send_hz:4.0f} Hz", _FG if stats.send_hz >= 45.0 else _AMBER),
        ]
        for i, (line, color) in enumerate(lines):
            self._screen.blit(self._font.render(line, True, color), (240, y + i * 19))

        flags = telemetry.flags if telemetry is not None else 0
        for i, (flag, label) in enumerate(_FLAG_LABELS):
            color = _RED if flags & flag else (55, 55, 62)
            x = 470 + i * 66
            pygame.draw.rect(self._screen, color, (x, y + 2, 60, 22))
            self._screen.blit(self._small.render(label, True, _FG), (x + 6, y + 8))

        if stale:
            self._screen.blit(self._big.render("LINK LOST", True, _RED), (self._width - 190, y))

        progress = self._tracker.reset_progress(now)
        if progress > 0.0:
            pygame.draw.rect(self._screen, (55, 55, 62), (16, y + 40, 300, 12))
            pygame.draw.rect(self._screen, _AMBER, (16, y + 40, int(300 * progress), 12))
            self._screen.blit(self._small.render("hold R to reset...", True, _AMBER), (322, y + 40))

        if self._tracker.mode == "toggle":
            # 토글은 "놓으면 즉시 멈춘다"는 성질이 없다. 그 상태임을 잊으면 위험하므로
            # 항상 눈에 띄게 표시한다.
            badge = "CLUTCH: TOGGLE - does NOT stop when you let go"
            text = self._font.render(badge, True, _AMBER)
            box = text.get_rect().inflate(12, 6)
            box.topleft = (16, self._height - 52)
            pygame.draw.rect(self._screen, (60, 45, 15), box)
            pygame.draw.rect(self._screen, _AMBER, box, width=1)
            self._screen.blit(text, (22, self._height - 49))
            clutch_help = "SPACE=toggle clutch"
        else:
            clutch_help = "SPACE hold=clutch"

        help_text = f"{clutch_help}   R hold 3s=reset   M=mock motion   ESC=quit"
        self._screen.blit(self._small.render(help_text, True, _DIM), (16, self._height - 22))

    def close(self) -> None:
        pygame.quit()
