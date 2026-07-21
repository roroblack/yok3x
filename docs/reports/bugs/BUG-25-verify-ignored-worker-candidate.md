# BUG-25 · verify가 워커 후보 대신 원본 트리를 검사

- **상태**: 수정 완료 (2026-07-21)
- **심각도**: 높음(게이트 신호와 캘리브레이션 라벨이 평가 후보와 무관)
- **영역**: `yok3x/orchestrator.py` — producer-reviewer verify

## 증상

워커는 파일을 직접 쓰지 않고 응답의 `file:` 블록으로 후보를 내는데 `_run_verify`는 원본 `workdir`에서
실행됐다. 따라서 고장 난 원본을 고친 후보도 실패하고, 반대로 원본이 통과하면 고장 후보도 통과할 수 있었다.

## 근본 원인

라운드 산출물 파싱과 verify 실행 사이에 후보를 파일 트리로 구성하는 단계가 없었다. F1-b는 오염 라벨을
`original_tree`로 격리했지만 실제 게이트 입력은 바로잡지 않았다.

## 수정

`verify_cmd`·`workdir`·`file:` 후보가 모두 있으면 무시 규칙과 파일수 상한을 적용해 workdir를 시스템 temp로
복사하고, 기존 artifacts 경로 검증 및 resolve/심볼릭 방어 후 후보를 복사본에만 쓴다. verify는 복사본 cwd에서
실행하며 결과 scope를 `candidate`로 기록한다. 준비 실패·상한 초과는 로그 후 원본 verify로 폴백하고 temp는
`finally`에서 삭제한다.

## 검증

- 고장 원본 + 수정 후보: staged verify 통과, 원본 SHA-256 및 내용 불변.
- 고장 후보: candidate scope에서 실패.
- strict 게이트가 후보 통과 신호를 사용하고 calibration scope가 candidate임을 통합 테스트로 확인.
- copytree 실패·파일수 상한 초과 폴백과 모든 임시 디렉터리 정리를 확인.

## 교훈

텍스트 후보를 평가하는 게이트는 반드시 그 후보를 실행 가능한 격리 상태로 구성해야 한다. 검증 대상(scope)은
결과와 함께 기록하고, 원본 폴백 관측은 후보의 지상진실 라벨로 사용하지 않는다.
