# BUG-23 · 산출물 게시(materialize) 결과가 status.json에서 유실

- **상태**: 수정 완료 (2026-07-19)
- **심각도**: 중간(기능은 동작하나 감사 기록 유실 — E2E 검증에서 발견)
- **영역**: `yok3x/orchestrator.py` — `_finish`

## 증상
materialize가 파일은 정상 게시하는데(`[out] 게시 N개` 로그), 런의 `status.json`에 `materialized` 항목이
**남지 않는다**(None). "완성했다는데 파일이 있나?"를 status로 확인할 수 없어 감사·GUI 표시가 불가.

## 근본 원인
`_finish`가 상태를 **두 번** 저장했다:
1. `_save_status("done", {"materialized": mat})` — materialized 기록
2. (knot 저장 후) `_save_status("done")` — **extra 없이 다시 저장 → materialized 덮어씀**

`_save_status`는 매번 status.json을 통째로 다시 쓰므로 두 번째 호출이 첫 번째의 materialized를 지웠다.

## 수정
중간 저장을 없애고, **최종 `_save_status`에 materialized를 함께 실어 한 번만** 기록:
`self._save_status("done", {"materialized": mat} if mat.get("enabled") else None)`.

## 검증
- E2E 테스트 추가: 실제 `_finish`→파일 생성 + status.materialized(written·rejected·sha) 기록 확인 ·
  경로탈출(`../`) 차단 · 임시파일 잔여 없음 · disabled no-op · file 블록 없을 때 ok=False.
- 이 버그는 **E2E 검증에서만 드러났다**(스모크·단위테스트는 통과). codex 권고("파일쓰기 기능은
  실제 런→파일 생성 E2E까지 해야 완료")의 정당성을 입증.
