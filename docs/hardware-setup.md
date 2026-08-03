# 하드웨어 설정 기록

측정일: 2026-07-31 (2단계-A, 카메라 없음)

이 문서는 나중에 원격 사용자 3명에게 장비를 나눠줄 때 같은 절차를 반복해야 하므로 남긴다.

## 팔 4대

| 역할 | 시리얼 번호 | COM (변동 가능) | calibration_id | 읽기 레이트 |
|---|---|---|---|---|
| 리더 왼쪽 | `5B14113336` | COM3 | `leader_left` | 499 Hz |
| 리더 오른쪽 | `5B14030800` | COM6 | `leader_right` | 499 Hz |
| 팔로워 왼쪽 | `5B14033734` | COM5 | `follower_left` | 484 Hz |
| 팔로워 오른쪽 | `5B14031059` | COM4 | `follower_right` | 484 Hz |

**COM 번호는 참고용이다.** USB 재연결·재부팅으로 바뀌므로 설정에는 시리얼 번호를 쓴다.

USB-TTL 칩은 **CH343**이며 Windows 드라이버가 이미 설치되어 있다.

### 읽기 레이트에 대한 정정

스펙은 처음에 "시리얼 버스 왕복 6회/사이클이라 60Hz가 한계 근처"라고 적었는데 **틀렸다.**
lerobot 은 `sync_read` 로 6개 모터를 한 번에 읽으므로 실측 **484~499 Hz** 가 나온다.
제어 레이트 60Hz 는 8배 여유가 있다.

## 캘리브레이션 기준 자세

4대 모두 **같은 기준**으로 "중간 자세"를 잡아야 한다. 어긋나면 정렬 절차가 영원히
초록이 되지 않아 ENGAGED 로 갈 수 없다.

> 이번에 사용한 기준: (실제 사용한 자세를 여기에 적는다 — 예: 팔을 수직으로 세우고
> 팔꿈치는 곧게, 그리퍼는 반쯤 벌린 자세)

캘리브레이션 파일 위치:
```
~/.cache/huggingface/lerobot/calibration/robots/so_follower/follower_{left,right}.json
~/.cache/huggingface/lerobot/calibration/teleoperators/so_leader/leader_{left,right}.json
```

## 좌우 매핑 검증 결과

`python -m tools.probe_hardware --check-sides` 로 한 팔만 움직였을 때 배열의 그 절반만
변하는지 확인했다.

| 팔 | 움직인 쪽 최대 변화 | 반대쪽 최대 변화 |
|---|---|---|
| 리더 왼쪽 | 77.2 | 0.1 |
| 리더 오른쪽 | 136.4 | 0.2 |
| 팔로워 왼쪽 | 62.2 | 0.2 |
| 팔로워 오른쪽 | 79.8 | 0.2 |

반대쪽 0.1~0.2 는 인코더 노이즈다. 좌우 매핑이 물리적으로 정확하다.

**정렬 임계값 3.0도는 이 노이즈(0.2도)의 15배**이므로 오탐 여지가 없다.

## 실측 관절 한계

캘리브레이션 파일의 `range_min`/`range_max`(raw 인코더 틱)를 도로 변환했다:

```
도 = (raw - (range_min + range_max) / 2) * 360 / 4095
```

| 관절 | 실측 가동범위 | 설정값 (양쪽 8도 좁힘) |
|---|---|---|
| left_shoulder_pan | ±109.4 | ±101.0 |
| left_shoulder_lift | ±110.2 | ±102.0 |
| left_elbow_flex | ±97.3 | ±89.0 |
| left_wrist_flex | ±101.3 | ±93.0 |
| left_wrist_roll | ±180.0 (한 바퀴) | ±170.0 |
| left_gripper | — | 0~100 (퍼센트) |
| right_shoulder_pan | ±112.8 | ±104.0 |
| right_shoulder_lift | ±105.4 | ±97.0 |
| right_elbow_flex | ±98.1 | ±90.0 |
| right_wrist_flex | ±104.2 | ±96.0 |
| right_wrist_roll | ±180.0 (한 바퀴) | ±170.0 |
| right_gripper | — | 0~100 (퍼센트) |

> **이전 설정의 ±120도는 실제 가동범위보다 넓어서 클램프가 사실상 무효였다.**

**이것은 팔의 기구학적 한계다.** 작업대에 실제 장비를 배치한 뒤에는 팔이 그 장비를
치는 자세를 배제하도록 더 좁혀야 한다.

## 카메라

**2단계-A 에서는 카메라 없음.** USB 포트 4개가 팔 4대로 다 찼다.
`config/workbench.yaml` 의 `cameras: []`.

발견된 카메라: 인덱스 0 (노트북 내장 웹캠). 작업대에 놓을 위치가 아니라 사용하지 않는다.

| 이름 | 인덱스 | 실제 해상도 | 실측 fps |
|---|---|---|---|
| front | (2단계-B) | | |
| wrist_left | (2단계-B) | | |
| wrist_right | (2단계-B) | | |

포트 부족은 2단계 한정 문제다. 3단계에서 PC 를 나누면 카메라는 작업대 PC(팔로워 2대,
USB 2개 사용)로 가므로 해소된다.

## wrist_roll 영점 (필수 절차)

**캘리브레이션의 "같은 중간 자세"에는 그리퍼가 어느 방향을 보는지도 포함된다.**

lerobot 은 wrist_roll 을 한 바퀴 도는 관절로 취급해 가동범위를 측정하지 않는다
(`range_min=0, range_max=4095`). 영점은 순전히 `set_half_turn_homings()` 를 부른
순간의 자세로 정해진다. 따라서 캘리브레이션할 때 리더와 팔로워의 그리퍼 회전
방향이 달랐다면 그 차이가 영점에 박힌다.

실측: 양팔 모두 32~39도 어긋났고, **리더는 손잡이가 책상에 닿아 그 각도까지 돌릴
수 없어** 정렬 절차를 통과할 수 없었다.

해결:

```bash
python -m tools.rezero_wrist_roll --dry-run   # 현재 어긋난 양만 확인
python -m tools.rezero_wrist_roll             # 영점 다시 잡기
```

**기준 방향은 리더가 정한다.** 손잡이가 범위를 제한하는 쪽이므로, 리더가 편하게
잡을 수 있는 방향을 고르고 팔로워를 손으로 거기에 맞춘다. 어느 각도인지는 중요하지
않다 — 그 방향이 4대 모두의 0도가 된다.

## 호밍 목표 자세 (home_pose)

`python -m tools.capture_home_pose` 로 **리더에서** 떠온다. 팔로워에서 뜨면 리더가
도달 못 하는 자세를 목표로 삼는다.

2026-07-31 에 떠온 값은 `config/workbench.yaml` 의 `safety.home_pose` 에 있다.

**떠올 때 주의할 것:**

- **각 관절의 가동범위 중간쯤**을 고른다. 실측에서 `elbow_flex` 가 기록된 최대치
  (97.3)에 붙어 나와 `joint_limits`(89/90) 밖이었고, 85.0 으로 낮춰 넣었다.
- **그리퍼는 어떤 값으로 떠도 애매하다.** 거의 닫힌 값(실측 2.1%, 1.4%)이면 호밍이
  쥐고 있던 것을 더 조이고, 열린 값이면 떨어뜨린다. 미결 항목 참조.

## 실물에서 발견한 결함

**lerobot 의 `enable_torque`/`disable_torque` 는 `num_retry=0` 이다.**

1Mbaud 반이중 버스에 서보 6개가 데이지체인이면 패킷 유실은 정상적으로 일어나는데,
하나만 흘려도 `connect()` 전체가 죽는다:

```
ConnectionError: Failed to write 'Lock' on id_=6 with '1' after 1 tries.
[TxRxResult] There is no status packet!
```

실패 직후 `--scan-motors` 로 24개 모터(4팔 × 6개)를 `num_retry=2` 로 핑하니 **전부
응답**했다. 모터가 죽은 것이 아니었다.

lerobot 자신도 `disconnect()` 경로에는 `disable_torque(num_retry=5)` 를 쓴다.
**켜는 경로에만 방어가 빠져 있다.**

→ 양쪽 어댑터가 연결을 4회 재시도하고, 실패 시 반쯤 열린 포트를 정리하며,
토크 쓰기에 `num_retry=5` 를 쓴다.

**2. 연결만 해도 팔로워가 통전된다.**

lerobot 의 `torque_disabled()` 컨텍스트 매니저는 문서에 "종료 시 토크를 반드시 다시
켠다"고 명시돼 있고, `SOFollower.configure()` 가 그것을 쓴다. 스펙 §5.1 의
DISCONNECTED 는 토크가 꺼진 상태이므로 어댑터가 연결 직후 명시적으로 끈다.
실측 확인: 12관절 전부 `Torque_Enable=0`.

**3. 시리얼 포트 동시 접근으로 클라이언트가 죽었다.**

```
ConnectionError: Failed to sync read 'Present_Position' ... [TxRxResult] Port is in use!
```

제어 송신을 별도 스레드로 분리하면서 HUD 루프의 리더 읽기를 지우지 않아, 두 스레드가
같은 시리얼 버스를 동시에 읽었다. lerobot 의 `MotorsBus` 는 스레드 안전하지 않다.
mock 은 순수 계산이라 이 버그를 잡을 수 없었다.

→ 장치의 소유자는 송신 스레드 하나이고, HUD 는 `LeaderSender.last_joints` 를 본다.

**4. wrist_roll 영점 불일치** — 위 절 참조.

**5-1. 메인 스레드에서 시리얼 포트를 열거하면 실패한다 (WinError 87).**

작업대 PC 에서 서버가 포트 조회에서 죽었다:

```
OSError: [WinError 87] 매개 변수가 틀립니다
  at list_ports_windows.py:247  SetupDiClassGuidsFromNameW("Ports", ...)
```

죽는 지점은 **장치를 하나도 건드리기 전**, 클래스 이름을 GUID 로 바꾸는 첫 호출이다.
따라서 USB 상태·열린 포트·COM 번호와는 무관했다.

측정으로 확인한 조건:

* **메인 스레드만 고장난다.** 같은 프로세스에서 새 스레드로 부르면 성공하고, 그 직후
  메인 스레드로 부르면 실패한다.
* `cv2` 와 `lerobot.motors` 가 **둘 다** 로드된 뒤, 그 스레드의 **첫 SetupAPI 호출**에서만.
  둘 중 하나만으로는 재현되지 않는다.
* **import 순서가 중요하다.** `cv2` → `lerobot` 이면 깨지고 `lerobot` → `cv2` 는 안 깨진다.
* 재시도로는 낫지 않는다 (10초간 50회 재시도 → 50회 다 실패).
* `setupapi.dll` 은 정상 경로. DLL 하이재킹이 아니다.

해석: cv2 와 torch 가 끌어오는 수십 개 DLL 이 **이미 존재하던** 메인 스레드의 스레드
로컬 저장소를 고갈시킨다. 나중에 만든 스레드는 그 DLL 들이 다 올라온 뒤에 생기므로
영향을 받지 않는다.

→ `common/serial_ports.py` 의 `_comports_off_main_thread()` — 조회를 짧은 수명의 새
스레드에서 한다. import 순서를 바꿔서 피할 수도 있지만, 누가 import 한 줄을 옮기면
조용히 재발한다.

**5-1b. 같은 원인으로 카메라도 안 열렸다 — 패턴으로 기억할 것.**

카메라 3대를 붙이자 서버에서 3대 다 열리지 않았다:

```
VIDEOIO(DSHOW): backend is generally available but can't be used to capture by index
CameraOpenError: camera 0 (front): cannot open device index 3
```

그런데 `probe_hardware --cameras` 는 같은 순간 4대를 다 열었다. 차이는 lerobot 이었다:

```
import cv2                        -> VideoCapture(3, CAP_DSHOW) 열림
import cv2, lerobot...            -> 안 열림   (서버와 같은 순서)
import lerobot..., cv2            -> 열림
```

**5-1 과 같은 뿌리다.** 그때는 SetupAPI(`SetupDiClassGuidsFromNameW`), 이번엔
DirectShow. 둘 다 **Windows 장치 API** 이고, 둘 다 **메인 스레드에서만** 고장난다.

→ `CameraPublisher._loop` 가 장치를 연다. 짧은 스레드에서 열어 객체만 넘기는 방식은
쓰지 않았다 — DirectShow 는 COM 기반이라 만든 스레드의 아파트먼트가 중요하고, 만든
스레드와 읽는 스레드가 다르면 또 다른 문제가 생긴다. 캡처 스레드가 이미 읽기를
담당하므로 거기서 열면 **열기와 읽기가 같은 스레드**가 된다.

> **패턴: 이 프로세스에서 Windows 장치 API 를 메인 스레드에서 부르지 않는다.**
> cv2 와 torch/lerobot 이 끌어오는 DLL 무리가 이미 존재하던 메인 스레드의 스레드
> 로컬 저장소를 고갈시키는 것으로 보인다. 나중에 만든 스레드는 영향이 없다.
> 새로 장치 열거·열기를 추가한다면 **처음부터 스레드에서** 하고, 가능하면 그것을
> 계속 쓰는 스레드에서 해라.

**5-2. 진단 도구가 스스로에게 예방접종을 했다 (방법론적 교훈).**

한 스레드에서 `comports()` 가 한 번 성공하면 그 스레드는 그 뒤로 계속 성공한다.
그런데 당시 진단 도구들이 **전부 시작하자마자 열거부터 해봤다** — `probe_startup` 0단계,
`server --diagnose` A단계. 도구가 스스로를 고쳐 놓고 통과한 것이다.

그 결과 그 도구들이 "무죄"로 판정한 단계들(소켓 bind, `SO_EXCLUSIVEADDRUSE`, cv2, numpy,
열린 포트)은 **실제로는 검증된 적이 없었다.** 잘못된 배제가 쌓이면서 여러 라운드를 헛돌았고,
그 사이에 틀린 수정이 두 번 커밋됐다:

* `c9252d6` "일시적 실패 → 재시도" — 재시도로는 낫지 않는다
* `2e3fce5` "열린 포트가 열거를 방해" — 무관하다 (포트 조회를 앞으로 모은 것 자체는
  다른 이유로 옳아서 남겨 두었다)

**교훈: 상태를 바꾸는 동작을 진단할 때는 프로세스마다 딱 한 번만 재야 한다.**
"먼저 정상인지 확인하고 시작"하는 습관이 바로 그 상태를 없앤다.

**5. HOLD 이후 정렬 불가** — HOLD 가 걸린 자세가 리더로 도달 불가능하면 정렬을 다시
시작할 방법이 없었다. → `HOMING` 상태 추가 (스펙 §5.1.1).

**5-3. 캘리브레이션 파일을 저장소 안에 두면 lerobot 이 안 읽는다.**

`C:\teleop\robots\so_follower\follower_left.json` 처럼 저장소 안에 두었더니
`has no calibration registered` 로그가 512KB 폭주했다. 폴더 구조는 맞았고 **뿌리만 달랐다.**
lerobot 이 읽는 곳은 오직 여기다:

```
$HF_LEROBOT_CALIBRATION / robots / so_follower / <id>.json
  (기본값 = %USERPROFILE%\.cache\huggingface\lerobot\calibration)
```

정본은 한 곳뿐이어야 한다. 저장소 안 사본은 지우고 `.gitignore` 에 `robots/`, `_cal/` 를
넣었다. PC 사이 이동은 `tools/move_calibration.py` 를 쓴다 — 그 도구는 항상 캐시 쪽에
넣고 넣은 경로를 출력한다.

**6. 양팔 조종에 손이 부족하다** — 리더 2개를 양손으로 잡으면 클러치를 누를 손이
없다. 첫 양팔 시험에 **다른 사람이 스페이스를 눌러줘야** 했다. 풋페달은 선택이 아니라
필수다. 임시로 `--clutch toggle` 을 쓸 수 있으나 "놓으면 즉시 멈춤" 성질을 잃는다.

## 결정된 것

**호밍은 그리퍼를 건드리지 않는다.** 쥐고 있던 것을 조이지도, 떨어뜨리지도 않는다.
`home_pose` 의 그리퍼 두 값은 설정에 남아 있지만 사용되지 않는다 (스펙 §5.1.1).

**클러치는 토글로 쓴다. 풋페달은 도입하지 않는다.**

`--clutch toggle` 이 실사용 기본이다. hold 모드의 "놓으면 즉시 멈춤" 성질을 잃지만,
속도 클램프(90도/초)가 급가속을 막으므로 리더를 놓쳐도 팔로워는 1초쯤에 걸쳐
천천히 움직이고 그 사이 스페이스나 ESC 로 멈출 수 있다.

"리더 떨어뜨림 자동 감지"를 검토했으나 **채택하지 않았다.** 팔을 의도적으로 빠르게
내리는 것을 낙하로 오인하면 조작감이 나빠지고, 그 불편이 실제 위험보다 크다고
판단했다. 실사용에서 팔을 격하게 다루지 않는다는 전제다.

## 미결 항목

- **`left_elbow_flex` 여유 4도** — 캘리브레이션 때 팔꿈치를 끝까지 젖히지 않았을
  가능성. 실제 기구학적 스톱을 확인해 `joint_limits` 를 넓힐지 판단.
- **24시간 소킹** — 1단계에서 미루었고 아직 하지 않았다. 실물 팔로 3단계에서 함께.
- **호밍 실물 검증** — HOMING 상태는 단위 테스트만 통과했고 실제 팔에서 아직 돌려보지
  않았다. 3단계 전에 확인해야 한다.

## 다시 할 때 (원격 사용자에게 장비 배포 시)

```bash
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation"
PY="C:/Users/flash/miniconda3/envs/lerobot/python.exe"

# 1. 시리얼 번호 확인 (팔을 하나씩 뽑아 어느 줄이 사라지는지 본다)
$PY -m tools.probe_hardware --ports

# 2. 배선·전원 확인 (모터 1~6이 다 응답하는지)
$PY -m tools.probe_hardware --scan-motors

# 3. 팔마다 캘리브레이션 1회. 4대 모두 같은 기준 자세로.
cd "C:/Users/flash/lerobot"
$PY -m lerobot.scripts.lerobot_calibrate --teleop.type=so101_leader --teleop.port=COM? --teleop.id=leader_left
$PY -m lerobot.scripts.lerobot_calibrate --robot.type=so101_follower --robot.port=COM? --robot.id=follower_left

# 4. 관절각을 읽어 그리퍼가 0~100 퍼센트로 나오는지, 레이트가 60Hz 이상인지
cd "C:/Users/flash/Desktop/lerobot/remote teleoperation"
$PY -m tools.probe_hardware --arms --kind leader   --config config/home.yaml
$PY -m tools.probe_hardware --arms --kind follower --config config/workbench.yaml

# 5. 좌우가 뒤바뀌지 않았는지 (한 팔만 움직여 확인)
$PY -m tools.probe_hardware --check-sides --kind leader   --config config/home.yaml
$PY -m tools.probe_hardware --check-sides --kind follower --config config/workbench.yaml
```
