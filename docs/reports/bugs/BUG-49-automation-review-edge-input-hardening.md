# BUG-49 — 자동화·구조화 리뷰 엣지 입력 예외 및 오판

## 증상

계획서에 없던 입력 조합에서 자동화/구조화 리뷰 경로가 예외를 내거나, 안전한 기본 동작 대신 잘못된 판단을 했다.

## 근본 원인

- 위험어 판정이 토큰 경계 없이 부분 문자열을 검색해 `preview`를 `review` 위험어로 판정했다.
- 알 수 없는 `daily_pace.level`을 숫자 `used/cap` 과대값과 함께 받으면 경고 조정 경로로 진입했다.
- 재개 판정이 잘못된 `automation_mode` 타입/대소문자를 `ValueError` 그대로 전파했다.
- 구조화 리뷰 추출기가 비문자열 입력을 정규식에 전달했고, 균형 괄호 후보를 시작점마다 재스캔했다.
- canonical defect 서명이 malformed description/defect 항목을 직접 받으면 `AttributeError`/`KeyError`가 났다.

## 진단

엣지 입력 회귀 테스트로 재현했다. `None`, `preview`, unknown pace level + `used="999"/cap=1`, `Full`/리스트 모드, 5,000개 미완성 중괄호를 사용했다.

## 수정

- 위험어를 토큰 경계 기준으로 검색하고 `deserializ` 접두어만 기존 의도대로 유지했다.
- unknown pace level은 원래 추천을 보존하고 즉시 반환한다.
- `_resume_supported()`가 잘못된 모드를 재개 불가 사유로 반환한다.
- 리뷰 입력은 문자열이 아니면 legacy 폴백하고, 후보 추출은 단일 선형 균형 스캔으로 바꿨다.
- canonical 서명은 누락/비문자열 description을 안전하게 정규화하고 비-dict 항목을 건너뛴다.

## 검증

- 감사 회귀 및 기존 자동화/리뷰 테스트 통과.
- `python -m py_compile` 및 전체 `pytest -q`로 재검증.

## 교훈

외부/과거 상태 입력은 계산 전에 타입과 허용 열거값을 경계에서 확인해야 하며, 부분 문자열 휴리스틱은 토큰 경계를 가져야 한다.
