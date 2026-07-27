# BUG-40 · 조용한 열화 2건 — verify 거짓 실패(workdir 미설정)와 미보정 추정으로 전체 런 차단

- **상태**: 수정 완료 (2026-07-27)
- **심각도**: 높음(① 통과하는 산출물을 실패로 라벨링 → T-1 지상진실 오염 위험 ② 새 프로젝트에서 모든 런 차단)
- **영역**: `yok3x/orchestrator.py` `_run_round_verify`·`_run_task_file` · `yok3x/limits.py` `_probe_claude_statusline`
- **발견 경로**: T-2 파일럿 실행(실측) — 추정만으로는 드러나지 않았다.

## 증상

**① verify 거짓 실패**: T-2 파일럿 런1이 2라운드 내내 `[verify] 실패`로 기록됐다. 그런데 그 산출물
(`slug.py`)을 그대로 꺼내 수동 실행하니 **테스트 5개 전부 통과**했다. 리뷰어 점수는 10.0/9.0인데
게이트는 `verify_failed`로 반려 — 신호가 서로 모순.

**② 새 프로젝트 전체 차단**: `yok3x init` 직후 claude 한도가 **7d 995%**로 표시되고(실측은 3%),
가드가 `stop`을 내려 **어떤 런도 시작되지 않았다.** 메인 프로젝트의 보정값을 복사해 넣어야 진행됐다.

## 근본 원인

**①** `_run_round_verify`는 후보 스테이징(F1-f)에 `self.workdir`가 필요하다. task.json에 `workdir`가
없으면 `if not self.workdir` 분기로 **조용히** 원본 트리 검증으로 떨어진다. 그런데 `changes.mode=review`
에서는 산출물을 workdir에 쓰지 않으므로, 검증 대상 트리엔 **산출물이 아예 없다** → import 실패 →
**모든 라운드가 거짓 실패**. `verify_scope="original_tree"`로 기록돼 T-1 라벨에서는 제외되지만(설계가
방어), 사용자는 사유를 볼 수 없어 "왜 계속 실패하나"만 남는다.

**②** `_probe_claude_statusline`의 추정 폴백에 **비현실성 가드가 없었다.** oauth 경로에는 있다
(`est.ratio() <= 2.0`, BUG-15 교훈). 새 프로젝트는 `limit_*_tokens`가 0이라 plan 프리셋(max5x=500M)만
쓰는데, 트랜스크립트 7d 토큰은 캐시read 누적으로 5.36B → **1000%**. 이 값이 `ok=True`로 반환돼
가드가 그대로 stop 판정.

## 수정

- **①** `_run_round_verify`가 열화 사유를 **명시 로그**로 남긴다("workdir 미설정 → 후보 스테이징 불가 …
  실패가 거짓일 수 있다"). 추가로 런 시작 시 `verify_cmd`는 있는데 `workdir`가 없으면 `[warn]`을 한 번 출력
  (라운드별 로그보다 발견 쉬움). RULE §5.5(조용한 폴백 금지) 준수.
- **②** statusline 추정 폴백에도 oauth와 **동일한 비현실성 가드**를 적용 — `ratio > 2.0`이면 `ok=False`로
  두고 사유·해결책(`yok3x calibrate claude 7d <실제%>` / `limits.claude.plan`)을 error에 담는다.
  그러면 `check_backend`가 원장(sane) 폴백으로 넘어가 런이 차단되지 않는다.

## 검증

- 신규 테스트 3: 열화 사유 로그, 비현실 추정 거부(995% → ok=False, 안내문 포함), 정상 추정(42%)은 통과 유지.
- 실측 재확인: 새 프로젝트 guard `stop → ok`(원장 `daily_usd` 폴백), `[warn]`·`[verify-stage]` 두 경고 모두 출력.
- `workdir`를 설정한 파일럿 런2는 `[verify-stage] 후보 1파일 검증 → [verify] 통과 → 게이트 통과`로 정상. 346 passed.

## 교훈

**조용한 열화는 "기능이 고장난 것"과 구분되지 않는다.** 두 건 모두 코드는 설계대로 폴백했지만, 사유를
남기지 않아 ① 맞는 코드를 틀렸다고 라벨링하고 ② 새 사용자의 모든 런을 막았다. 폴백 지점마다
"왜 이 경로로 갔는지"를 남겨야 한다(RULE §5.5). 또한 **같은 성질의 가드는 모든 소스 경로에 일관 적용**해야
한다 — oauth엔 있고 statusline엔 없던 비현실성 가드가 정확히 그 구멍이었다.
관련: [[BUG-15]](미보정 추정 1003%), [[BUG-25]](verify가 후보 대신 원본을 검사), [[BUG-39]](관측 코드가 본작업을 깨뜨림).
