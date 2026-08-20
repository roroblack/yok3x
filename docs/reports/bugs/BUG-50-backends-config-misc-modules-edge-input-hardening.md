# BUG-50 — backends/config 및 기타 모듈 엣지 입력 하드닝 (worktree 오삭제 위험 포함)

## 증상

`yok3x/backends.py`·`yok3x/config.py`·`yok3x/worktree.py`·`yok3x/mcp_policy.py`·
`yok3x/artifacts.py`·`yok3x/knot.py`·`yok3x/triage.py`·`yok3x/calibration.py`에
계획서에 없던 입력/상태 조합에서 예외가 전파되거나(크래시), 안전하지 않은 기본
동작(가장 심각: 등록 안 된 임의 디렉터리를 조건 없이 `rmtree`)이 발생했다.

## 근본 원인

- **`worktree.remove()`**(가장 심각): `git worktree remove --force`가 실패하면
  "경로가 존재하면" 무조건 `shutil.rmtree(dest)`를 호출했다 — `dest`가 실제로
  git에 등록된 worktree인지 확인하지 않아, 호출부 버그나 경합으로 엉뚱한 경로가
  넘어오면 임의 디렉터리를 삭제할 위험이 있었다.
- `backends.py`: 멀티라인 프롬프트만 stdin으로 우회하고, 개행 없는 대형(128KiB+)
  입력이나 깨진 서로게이트/NUL 바이트가 섞인 프롬프트는 그대로 argv에 넣었다.
  `spec["command"]`가 빈 값/비-리스트면 `KeyError`/`TypeError`로 죽었고, subprocess
  생성 자체가 던지는 `OSError`/`ValueError`를 안 잡았다.
- `config.py`: `yok3x.json`/`backends.json`이 JSON 배열/스칼라(비-object)이거나
  과도하게 깊게 중첩되면 `AttributeError`/`RecursionError`가 그대로 전파됐다.
- `mcp_policy.py`: 전역 화이트리스트나 워커 설정이 dict가 아니거나, 요청 서버명이
  문자열이 아니면 예외가 났다(fail-closed 원칙과 어긋남).
- `artifacts.py`: 경로 인자가 문자열이 아니면(`None` 등) `_bad_path`가 죽었다.
- `knot.py`: `created`가 timezone-aware ISO, `now`가 naive(또는 반대)면 datetime
  뺄셈이 `TypeError`를 냈다.
- `triage.py`: `max_rounds`가 숫자로 변환 안 되는 값이면 `estimate_execution`이 죽었다.
- `calibration.py`: `read_calibration_jsonl`이 전체 라인을 리스트에 쌓았다가 슬라이싱해
  대형 파일에서 불필요하게 메모리를 썼고, `aggregate_calibration_statistics`가
  malformed record(비-dict, score 비숫자, verify_ok 비-bool)를 직접 받으면 죽었다.

## 진단

각 모듈에 대해 실제 엣지 입력(빈/None/타입 불일치/대형/깨진 유니코드/미등록 경로 등)으로
재현했다. `worktree.remove()`는 `git worktree list --porcelain`을 목킹해 "등록 안 된
경로"와 "등록된 고아 경로" 두 시나리오로 구분 재현했다.

## 수정

- `worktree.remove()`: git 제거 실패 시 `git worktree list --porcelain`으로 `dest`가
  실제 등록된 worktree인지 확인한 뒤에만 `rmtree`한다.
- `backends.py`: stdin 우회 조건에 NUL·인코딩 실패·128KiB 초과를 추가하고, 잘못된
  command 형식과 subprocess `OSError`/`ValueError`를 안전한 `BackendResult(ok=False)`로 반환.
- `config.py`: JSON 루트가 dict가 아니면 경고 후 기본값 폴백, 깊은 중첩은
  `RecursionError`를 잡아 기본값으로 폴백.
- `mcp_policy.py`: 비-dict 설정과 비-문자열 서버명을 fail-closed로 처리.
- `artifacts.py`: 비-문자열 경로를 즉시 거부.
- `knot.py`: naive/aware datetime을 비교 전에 맞춘다.
- `triage.py`: `max_rounds` 변환 실패 시 기본값(2)으로 폴백.
- `calibration.py`: `read_calibration_jsonl`이 `deque(maxlen=window)`로 최근 window만
  유지(메모리 상한), `aggregate_calibration_statistics`가 malformed record를
  `schema_mismatch` 사유로 건너뛴다.

## 검증

- 수정 파일 전체 `python -m py_compile` 통과.
- `tests/test_deep_audit_targets.py`(backends/config, 7개), `tests/test_misc_modules_hardening.py`
  (worktree/mcp_policy/artifacts/knot/triage/calibration, 11개) 신규 회귀 테스트.
- 전체 `pytest -q` 2회 독립 재실행 `614 passed, 1 skipped`로 안정.

## 교훈

파일시스템을 삭제하는 코드는 "존재하면 지운다"가 아니라 "삭제 대상이 우리가
관리하는 자원임을 확인한 뒤에만 지운다"는 원칙을 지켜야 한다(worktree.py가
가장 심각한 사례). 외부/과거 상태 입력은 항상 타입·범위를 경계에서 검증한다.
