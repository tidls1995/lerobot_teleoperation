# SO-101 Remote Teleoperation

집의 리더 암 2대로 작업대의 팔로워 암 2대를 카메라 영상을 보며 원격 조작한다.

- 설계: `docs/specs/2026-07-31-remote-teleoperation-design.md`
- 하드웨어 기록: `docs/hardware-setup.md`
- 작업대 PC 세팅: `docs/deployment.md`

## 구성

| | 작업대 PC | 집(개발) PC |
|---|---|---|
| 장치 | 팔로워 2대 + 카메라 3대 | 리더 2대 |
| 실행 | `workbench.server` | `home.client` |
| 포트 | UDP 5555 수신, TCP 5556 수신 | 없음 (먼저 접속한다) |

제어는 UDP(66바이트, 60Hz), 영상은 TCP(길이 프리픽스, 3채널 다중화)로 **완전히 분리**한다.
같은 연결에 태우면 영상 혼잡이 제어를 밀어내 팔이 끊긴다.

## 실행

작업대 PC:

    python -m workbench.server --config config/workbench.yaml

집 PC — 먼저 링크만 확인하고:

    python -m tools.check_link
    python -m home.client --config config/home.yaml --clutch toggle

한 PC 에서 mock 으로 돌리려면 두 설정의 `use_mock` 을 `true` 로 바꾼다.

## 조작

| 키 | 동작 |
|---|---|
| `SPACE` | 클러치. `--clutch hold` 면 누르고 있는 동안, `--clutch toggle` 이면 한 번 눌러 걸고 다시 눌러 푼다 |
| `R` 3초 | HOLD 해제. `home_pose` 가 설정돼 있으면 팔로워가 그 자세로 천천히 되돌아간다 |
| `ESC` | 종료 (송신이 끊겨 200ms 안에 팔이 멈춘다) |

## 테스트

    python -m pytest tests/ -v

안전 로직(`workbench/safety.py`)과 직렬화(`common/protocol.py`)는 네트워크·하드웨어
의존이 없는 순수 모듈이다. **사고를 막는 로직 전부를 로봇 없이 검증할 수 있다.**

## 진단 도구

    python -m tools.probe_hardware --ports          # 시리얼 번호
    python -m tools.probe_hardware --scan-motors    # 모터 1~6 응답 확인
    python -m tools.probe_hardware --arms --kind leader --config config/home.yaml
    python -m tools.probe_hardware --check-sides --kind leader --config config/home.yaml
    python -m tools.probe_hardware --cameras        # 카메라 인덱스
    python -m tools.check_link                      # 두 PC 사이 링크
    python -m tools.move_calibration --list         # 캘리브레이션 파일
    python -m tools.rezero_wrist_roll --dry-run     # wrist_roll 어긋난 양
    python -m tools.capture_home_pose               # 호밍 목표 자세 뜨기

## 주의

**캘리브레이션 파일은 이 저장소에 없다.** `~/.cache/huggingface/lerobot/calibration/`
에 있으므로 팔을 다른 PC 로 옮기면 `tools/move_calibration.py` 로 같이 옮겨야 한다.

**`common/protocol.py` 는 양쪽 PC 가 완전히 같아야 한다.** 어긋나면 서버가 패킷을
거부하고 화면은 DISCONNECTED 로 남는다. `tools/check_link.py` 가 이 경우를 구분해
알려준다.
