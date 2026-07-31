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

**또 하나:** lerobot 의 `torque_disabled()` 컨텍스트 매니저는 문서에 "종료 시 토크를
반드시 다시 켠다"고 명시돼 있다. 즉 팔로워를 **연결만 해도 통전된다.** 스펙 §5.1 의
DISCONNECTED 는 토크가 꺼진 상태이므로 어댑터가 연결 직후 명시적으로 끈다.
실측 확인: 12관절 전부 `Torque_Enable=0`.

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
