# SO-101 Remote Teleoperation

집의 리더 암 2대로 작업대의 팔로워 암 2대를 카메라 영상을 보며 원격 조작한다.

설계: `docs/specs/2026-07-31-remote-teleoperation-design.md`
계획: `docs/plans/2026-07-31-stage1-mock-teleoperation.md`

## 실행 (1단계 mock)

두 개의 터미널에서:

    python -m workbench.server --config config/workbench.yaml
    python -m home.client --config config/home.yaml

## 테스트

    python -m pytest tests/ -v
