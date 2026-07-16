# 하네스 멀티 에이전트 — GUI 기능 명세

> 목적: 이 문서를 GUI 빌더(디자이너/AI)에 던지면 바로 화면을 구성할 수 있게, 모든 기능·데이터·상태·컨트롤을 키워드로 정리.
> 백엔드는 이미 완성된 Python CLI(`harness`). GUI는 그 위의 프론트엔드 = **CLI 명령 실행 + 파일(harness.json / .harness/) 읽기·쓰기**.

---

## 0. 앱 한 줄 정의

Claude·Codex·Gemini를 묶어 **구독 한도를 지키며** 코딩 에이전트 루프(구현→리뷰→수정)를 돌리는 오케스트레이터. 한 모델이 만들고 다른 모델이 리뷰한다.

---

## 1. 화면(뷰) 구성

### 1.1 대시보드 (메인, = `mat`)
- 상단 바: `flavor 이름` · `가드 ON/OFF 토글` · `warn/stop 임계 표시` · `한도 새로고침 버튼` · `현재 시각`
- 3개 도구 한도 게이지 (claude / codex / gemini)
- 코치 메시지 패널
- 최근 런 진행 상태 리스트

### 1.2 한도 게이지 패널
도구별 카드:
- `도구 아이콘/이름`
- `사용률 바(0~100%)` + 퍼센트
- `소스 배지`: 실측 / 추정 / 원장
- `5시간 창`: % + 리셋 카운트다운
- `7일 창`: % + 리셋 카운트다운
- `plan type`(예: plus, max20)
- `호출 수` · `토큰` · `비용($)`
- 상태색: 정상(초록) / 경고(노랑) / 정지(빨강)

### 1.3 코치 패널
- 도구별 코칭 카드: `[여유/경고/정지] + 사유(5h/7d %) + 언제(리셋) + 권장 행동(어느 도구로 옮겨라)`
- 배지: 실측 / 추정 / 원장

### 1.4 런(실행) 진행 / 타임라인
- 런 목록: `run_id` · `패턴` · `상태` · `진행(step n/N)` · `마지막 워커`
- 런 상세: 스텝별 타임라인
  - `#번호` · `워커` · `상태(done/failed/skipped/blocked)` · `SCORE` · `체크리스트 이슈 ⚠`
- 최종 산출물 뷰어 (final_output.md)
- 실시간 로그 스트림 (run.log)

### 1.5 태스크 편집기
- 패턴 선택: `producer-reviewer` / `pipeline` / `fanout-fanin`
- 공통: `task(목표 프롬프트)`
- producer-reviewer: `producer` · `reviewer` · `max_rounds` · `pass_score`
- pipeline: `stages[] = {worker, kind, task}` (드래그 정렬)
- fanout-fanin: `workers[]` · `join_worker`
- 저장 → `task-*.json`, 실행 버튼(run / loop)

### 1.6 설정
- 가드: `enabled` · `soft_ratio 슬라이더` · `hard_ratio 슬라이더` · `use_real_limits` · `on_probe_failure(ledger/block/allow)`
- 요금제 선택(핵심): 도구별 `plan` 드롭다운(claude: pro/max5x/max20x, gemini: free/paid) → 한도 자동 설정. codex는 자동 감지 배지
- **캘리브레이트(정확 보정)**: "claude 앱의 실제 5h/7d %를 입력" 필드 → `harness calibrate`로 정확 상한 역산. 근사 프리셋보다 우선 안내
- 한도 probe: 도구별 `type`(codex_appserver / claude_transcripts / ledger / command)
- 예산(폴백): 도구별 daily_usd / daily_tokens / daily_calls
- 워커 편집: `backend` · `role(시스템 프롬프트)`
- flavor 전환: claude / codex / gemini-orchestrator
- backends.json: 도구별 `command 템플릿` · `type(cli/native/mcp/mock)` · `timeout`

### 1.7 knot 지식그물 뷰어
- 노트 목록/검색(`query`) · 노트 상세(frontmatter + 본문 + `[[위키링크]]`)
- `save` / `ingest` / `lint(깨진 링크·필드 누락)`

---

## 2. 핵심 데이터 포인트 (표시값)

| 그룹 | 키워드 |
|---|---|
| 한도 | 5시간 사용률 % · 7일 사용률 % · 리셋 카운트다운 · plan type · used_percent |
| 소스 | 실측(codex live) · 추정(claude transcript) · 원장(ledger) · stale(파일) |
| 사용량 | 호출 수(calls) · 입력/출력 토큰 · 총 토큰 · 비용(cost_usd) · duration_ms |
| 가드 | 상태 ok/warn/stop · soft_ratio · hard_ratio · enabled |
| 런 | run_id · 패턴 · state(running/done/aborted/stopped_by_guard) · step n/N · 마지막 워커 |
| 스텝 | index · worker · backend · status · SCORE · 체크리스트 이슈 |
| 워커 | claude-main · codex-main · codex-critic · gemini · role · backend |

---

## 3. 컨트롤 (버튼/입력)

- 가드: `ON/OFF 토글` · `soft/hard 임계 슬라이더`
- 실행: `Run(1회)` · `Loop(-n 반복)` · `Stop` · `한도 새로고침`
- 승인 게이트: `승인(y)` · `건너뜀(n)` · `중단(q)` — 각 스텝 실행 전
- flavor: `오케스트레이터 전환` 드롭다운
- 요금제: 도구별 `plan 선택` 드롭다운
- 모니터: `watch 자동 새로고침` 토글 + 주기(interval)
- knot: `검색` · `저장` · `가져오기` · `lint`

---

## 4. 상태 / 색상 규칙

| 상태 | 색 | 조건 |
|---|---|---|
| 정상(ok) | 초록 | 사용률 < soft_ratio(기본 80%) |
| 경고(warn) | 노랑 | soft_ratio ≤ 사용률 < hard_ratio |
| 정지(stop) | 빨강 | 사용률 ≥ hard_ratio(기본 100%) → 루프 자동 정지 |
| 런: running | 파랑(진행중) | |
| 런: done | 초록 | |
| 런: aborted / stopped_by_guard | 회색/빨강 | 사용자 중단 또는 가드 정지 |
| 스텝: blocked | 빨강 | 가드가 호출 차단 |

---

## 5. 실시간 / 알림 요소

- `watch` 모드: 대시보드 N초 주기 자동 갱신
- 한도 probe: codex 라이브 조회(~1.2s), 15초 캐시
- 이벤트 알림 후보: 가드 정지 · 게이트 대기 · 런 완료 · 리뷰 통과/실패

---

## 6. 배지 용어 사전

- **실측(live)**: codex app-server가 서버에서 지금 값을 받아옴 (가장 정확)
- **추정(estimate)**: claude transcript 롤링 합산 vs 설정 상한 (근사치)
- **원장(ledger)**: 자체 기록한 일일 사용량 vs 자체 예산 (자기 준수)
- **stale**: codex 라이브 실패 시 오래된 세션 파일 값 (부정확 가능)

---

## 7. GUI ↔ 백엔드 매핑 (데이터 소스)

| GUI 요소 | 소스 |
|---|---|
| 한도 게이지 | `harness limits` / `harness mat` 출력, 또는 `limits.probe()` |
| 코치 패널 | `harness coach` |
| 가드 토글 | `harness coach guard on\|off` (harness.json `guard.enabled`) |
| 런 목록/진행 | `.harness/runs/<run_id>/status.json` |
| 스텝 상세 | `.harness/runs/<run_id>/step_NN_<worker>.json` |
| 로그 | `.harness/runs/<run_id>/run.log` |
| 최종 산출물 | `.harness/runs/<run_id>/final_output.md` |
| 사용량 원장 | `.harness/usage.jsonl` |
| 설정 | `harness.json` · `backends.json` |
| 태스크 | `task-*.json` |
| knot | `knowledge/*.md` |
| 실행 | `harness run/loop <task.json>` |

> 구현 팁: GUI는 harness CLI를 subprocess로 호출하고 위 JSON/MD 파일을 읽으면 된다. 별도 API 서버 없이 파일+CLI만으로 완결.
