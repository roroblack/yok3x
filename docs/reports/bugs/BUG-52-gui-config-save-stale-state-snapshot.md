# BUG-52 — GUI 설정 저장 직후 stale state snapshot으로 되돌아감

## 증상

GUI의 상한 배분 방식에서 `분산(carry_smooth)`을 누르면 즉시 선택되지만 곧 이전
`유동(carry_fast)`으로 돌아갔다가 수 초 뒤 다시 분산으로 표시됐다. 같은
`POST /api/config` 경로를 쓰는 자동화 모드, autocalibrate, pace 및 정책 설정도
저장 직후 `load()`에서 동일한 일시적 되돌림이 가능했다.

## 원인

`GET /api/state`는 GUI 서버 다운을 막기 위해 최대 5초 된 `_GUI_STATE` snapshot을 즉시
반환한다. 이 동작은 올바르지만 `_apply_config()`가 `cfg.save_yok3x()` 후 snapshot을
갱신하지 않아, 저장 직후 GET이 디스크의 새 설정 대신 저장 전 snapshot을 반환했다.

수정 전 격리 재현에서 설정 객체/디스크 값은 `carry_smooth`였지만 직후 `_gui_state()`는
`carry_fast`를 반환했다. `_apply_config` 23.134ms, 캐시 조회 0.036ms였다.

## 수정

설정 저장 성공 후 요청 스레드에서 `build_state(cfg)`를 한 번 실행하고 완성된 snapshot과
`_GUI_STATE_BUILT_AT`을 함께 게시한다. `_GUI_STATE_GUARD`를 condition으로 확장해 기존
백그라운드 refresh가 진행 중이면 완료를 기다리고, 동일한 `_GUI_STATE_REFRESHING` 플래그로
두 build가 서로 덮어쓰지 않게 직렬화했다. 백그라운드와 동기 경로 모두 완료 시 대기자를
깨운다.

`GET /api/state` 경로는 변경하지 않았다. fresh snapshot이면 여전히 캐시를 반환하며 요청
스레드에서 `build_state()`를 실행하지 않는다. 동기 갱신은 기존 `build_state()`의 2초 이상
slow 로그를 그대로 사용하고 별도 duration 로그를 중복 추가하지 않았다.

## 검증

- 격리 서버를 `127.0.0.1:18760`에 띄워 `carry_fast` 상태에서
  `POST /api/config`로 `carry_smooth` 저장: `{"ok": true}`, 80.436ms.
- 직후 첫 `GET /api/state`: `carry_smooth`; 이어진 10회도 모두 `carry_smooth`.
- 워밍업 후 10회 GET 클라이언트 응답시간: 2.085~27.095ms, 중앙값 11.341ms.
- 단위 회귀 테스트는 저장 직후 새 snapshot 게시와 fresh-cache GET의 `build_state()` 미호출을
  각각 검증한다.
- `python -m py_compile yok3x/guiserver.py` 성공.
- 외부 임시 `--basetemp`를 쓴 전체 pytest는 깨끗한 실행 3회 모두
  `617 passed, 1 skipped`였다. 그 사이 1회는 기존 병렬 타이밍 테스트
  `test_parallel_limits_each_backend_with_semaphore`가 기대 동시성 2 대신 1을 관측해 실패했으며,
  해당 테스트만 독립 basetemp로 3회 재실행해 모두 통과했다.

## 인접 POST 경로 검토

`/api/run`과 task 저장/삭제/이름 변경도 성공 후 `load()`를 호출하므로 task/queue 관련 snapshot이
잠시 stale일 수 있다. 다만 이 경로까지 동기 full-state build를 넣으면 실행 시작/CRUD 응답 자체를
지연시키며 이번 설정 토글 회귀보다 범위가 커진다. 사용자 설정을 공통 처리하는 `_apply_config()`를
확실히 수정하는 최소 변경으로 한정하고, 해당 경로의 별도 캐시 일관성 정책은 후속 대상으로 남겼다.
`/api/sync/claim_action`은 결과를 POST 응답으로 직접 표시하고 즉시 `load()`하지 않는다.
