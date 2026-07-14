# HISTORY.md — 변경 이력

형식: `버전 · 날짜 시간 — 변경 요약`. 갱신할 때마다 맨 위에 새 항목을 추가한다.

---

## v3.5.0 · 2026-07-13 — 릴리스: CLI 모델 동적조회 · P3 오프라인 폴백 · effort · knot consolidation

- 이번 사이클 성과를 v3.5.0으로 묶음: **CLI 모델 동적조회**(claude `/v1/models`·codex 캐시·gemini 번들
  GEMINI_MODELS, 하드코딩 제거) · **P3 오프라인 폴백**(로컬 OpenAI 호환, urllib만·의존성0, GUI on/off
  토글) · **추론강도(effort)** claude(`--effort`)·codex(`-c model_reasoning_effort`) 통과 + 보드 드롭다운 ·
  **knot consolidation**(Mem0식 요점저장·중복통합·최신성감쇠) · claude stale-while-error·1003% 오표시
  차단 · 버전 단일출처(§5.6 GUI 승인규율 신설).
- `release/yok3x-v3.5.0.zip`(41파일, 내부 `__version__` 3.5.0 검증) + `backup/versions/` 보존, VERSIONS.md
  항목 추가. mock 스모크 테스트(init/setup/run/gui/limits) 통과. pytest 57 passed.
- 미완(정직): gemini effort는 settings.json thinkingLevel 경유라 라이브 검증이 gemini 레이트리밋으로
  보류(GUI dim 유지). 환경 회복 시 연결 예정.

## 미출시(dev) · 2026-07-13 — knot consolidation(Mem0식, 의존성 0) — 요점저장·중복통합·최신성감쇠

- Mem0 평가 권고 이행(벡터DB·추가 LLM 없이): ① 최신성 감쇠(`_recency_weight`, `query` 점수에 반감기
  감쇠 곱 — `knot.recency_halflife_days` 기본 90) ② 중복 통합(`lint`에 노트쌍 유사도≥`dedup_threshold`
  0.6면 '중복 후보' 표시, `_similarity`=태그·링크·제목 자카드) ③ 요점 저장(`extract_key_points`가
  SELF-CHECK·SCORE·결정 신호만 응축, orchestrator `_finish`가 런 저장 시 사용 — 지식그물 비대 억제).
- 의존성 0·설정 조절 가능. 신규 테스트 5. 리포트 `reports/v3.5.0-knot-consolidation-2026-07-13-1600.md`.

## 미출시(dev) · 2026-07-13 — 오프라인 폴백(P3) on/off GUI 토글 (사용자 요청)

- 사용자가 '로컬 모델 사용을 끄는 기능'을 명시 요청 → 기존 폴오버(P2) 토글과 동일 패턴으로 **오프라인
  폴백(P3) on/off 토글** 추가(`#swof`/`toggleOffline`). build_state guard.offline 노출, `/api/config`
  offline_enabled → `guard.degrade.offline_enabled`. 기본 ON. **끄면 로컬 미사용**(클라우드 stop 시 정지).
- 로컬 모델은 P3 폴백(클라우드 전멸+로컬 서버 도달 시)으로만 자동 사용됐고, 이제 GUI에서 끌 수 있음.
  라이브: 토글 라운드트립 ok(ON→OFF→ON), JS 에러 0.

## 미출시(dev) · 2026-07-14 — GUI 작업(task) 관리 CRUD (v3.8.0 계획 구현)

- 저장된 작업 **저장/열기/편집/삭제** — guiserver: `_save_task`/`_load_task`/`_delete_task`(원자적
  temp→replace), `_task_path`(형식·경로순회 방어, root 하위만), `_slug_task_name`(유니코드=한글 작업명),
  `_validate_task_spec`. API `/api/task` GET(열기)·POST(저장)·`/api/task/delete`. spec 검증은 /api/run과 공유.
- GUI: 콘솔에 [💾 작업 저장][▶ 실행][열기][🗑] + `buildSpec` 공용화. 저장 라벨=작업별 콘솔 연동.
- codex 논의 반영(task-*.json·원자적쓰기·경로순회 방어·이름중복). 라이브 검증(한글 slug·경로순회 차단·
  전체 CRUD 라운드트립), JS 에러 0. 신규 테스트 2. 외부 아이디어 채택 원장 `ADOPTIONS.md` 신설.

## 미출시(dev) · 2026-07-14 — 자기오염 루프 차단(계산기 실패 근본 수정) + lint 오탐 제거

- **버그**: "간단한 계산기 만들어줘"가 워커에 빈 작업처럼 전달돼 실패. 원인은 **자기오염 피드백 루프** —
  `_finish`가 런 '출력'을 brief.md에 덮어쓰고 knot에 저장 → 다음 런 프롬프트에 주입 → 워커가 직전
  실패 출력("빈 작업입니다")을 그대로 따라함 → 또 저장. task는 정상 전달됐으나 주입된 오염이 워커를 오도.
- **수정**: (1) `_finish`가 brief.md에 런 출력을 안 쓴다(brief.md=사용자 컨텍스트 전용). (2)
  `context_for_prompt`가 자동 런 노트(source=orchestrator)를 프롬프트에 주입 안 함(이력·검색용으론 유지).
  (3) `lint`가 런 노트를 검사 제외 → 워커 출력의 `[[csv-stream]]` 등 텍스트가 '깨진 링크'로 오탐되던 것 해결.
- 검증: 수정 후 프롬프트=`[작업] 계산기`만(204자, brief/knot 주입 0), lint 이슈 0. 회귀 테스트 3.

## 미출시(dev) · 2026-07-14 — 작업(task)별 콘솔 뷰 (v3.7.0 계획 구현)

- 런에 **`label`** 도입(작업 그룹키). `orchestrator`가 status.json에 기록, `run_task_file`이 spec.label →
  없으면 task 파일명 폴백(인라인은 무제목). `_recent_runs`가 label 노출, `runs_max`(기본 20)로 히스토리↑.
- GUI(콘솔): **작업별 보기 필터 드롭다운**(전체/라벨(개수)/무제목) — 선택 시 그 작업 런만 스텝상세까지.
  런 입력폼에 **라벨 필드** 추가(sendTask가 spec.label 전송). codex 논의 반영(라벨 그룹키·필터드롭다운·
  자유 task텍스트 그룹핑 지양). 라이브 검증(라벨 런 2개→'슬러그 함수 (2)' 그룹·필터 동작), JS 에러 0.
- 신규 테스트 3(label 저장·노출·파일명폴백·인라인 무제목). 백엔드(P1·P2)+GUI(P3·P4) 완료.

## v3.6.0 · 2026-07-14 — 릴리스: 주간쿼터 하루 페이싱(일일 소비 캡)

핵심: 주간(7d) 구독 쿼터를 **하루 단위로 페이싱**. "하루에 주간쿼터의 N%(기본 14%p≈1/7)만" 설정 →
도달 시 경고 또는 정지+승인 재개. 아래 dev 항목들(2026-07-13~14)을 묶어 릴리스.
- 측정: 오늘소비 = 현재 7d% − 그날 첫 관측 스냅샷의 **양의 증분 누적**(롤오프 상쇄 완화, `.yok3x/pace.json`).
- codex 리뷰(SCORE 4/10) 반영 하드닝: **sticky pause**(cap 도달 후 값이 낮아지거나 probe 실패해도
  정지 유지, 승인/자정만 해제), **실측 7d에만** 적용(추정 오정지 방지), 승인일 전체 우회, 원자적 상태쓰기.
- 3면 노출: CLI `yok3x pace status|approve`, config `guard.daily_pace`, GUI 설정탭(on/off·상한 슬라이더·모드·
  도구별 소비 미터·재개 승인·기본값 버튼). 기본 opt-in(off), 라이브 7d 있는 claude/codex 대상.
- 릴리스: `release/yok3x-v3.6.0.zip`(42파일) + `backup/versions/` 보존 + VERSIONS 갱신. 60 tests + 스모크
  (init/setup/run/pace) 통과. 스코프 밖: Mem0 consolidation은 v3.5.0에 이미 포함(재확인), 외부 프레임워크
  (LangGraph 등) 미사용(의존성0 유지).

## 미출시(dev) · 2026-07-14 — 하루 페이싱 GUI 개선(사용자 피드백)

- pace 상태 메시지 백엔드별 구분: claude="실측 7d 대기(토큰만료/rate-limit — 쓰면 갱신)",
  gemini="주간 API 없음 — 페이싱 대상 아님(원장)", codex=실측 미터. (기존엔 셋 다 "실측 7d 없음")
- 진단: claude=토큰 만료(일시적, 사용 시 자동 갱신) · gemini=주간 사용량 API 자체가 없음(구조적) ·
  codex=정상(7d=7%). 즉 페이싱은 codex에서 실동작, claude는 실측 복구 시 적용.
- 하루 상한 **↺ 기본값(14%p) 버튼** 추가. mode 버튼 **낙관적 하이라이트**(클릭 즉시 반영) + 저장 인디케이터.
- 설정·한도 탭에 **"모든 설정 변경 즉시 자동 저장"** 안내(별도 저장 버튼 불필요 명시).
- codex 요금제: app-server가 plan_type 자동 감지(plus 등) — 별도 설정 불필요.

## 미출시(dev) · 2026-07-14 — 하루 페이싱: 기본 14%p · codex 리뷰 반영 하드닝 · GUI(P5)

- 기본 하루 상한 `pct_of_weekly` 0.2→**0.14**(≈주간 1/7, 균등 페이싱).
- **codex 리뷰(SCORE 4/10) 반영** — 진짜 결함 보완: (1)pause **sticky block**(cap 도달 후 롤오프로 값이
  낮아져도 자동 재개 안 함, 승인/자정만 해제) (2)probe 실패해도 저장된 block으로 정지 유지
  (`pace_block_active`, 원장 폴백 경로) (3)**실측(real) 7d에만** 페이싱(미보정 추정 오정지 방지)
  (4)승인된 날은 페이싱 전체 우회 (5)오늘소비=**양의 증분 누적**(롤오프 상쇄 완화) (6)pace.json **원자적
  쓰기**+손상파일 백업(조용한 리셋으로 cap 우회 방지) (7)값 검증/클램프·finite 체크 (8)정확한 '7d' 창
  선택 (9)pace 채택 시 ratio=used/cap. 과설계 지적(별도 tz 등)은 로컬 실행 특성상 보류.
- **P5 GUI(설정·한도 탭)**: 하루 페이싱 on/off·상한(%p) 슬라이더·모드(경고만/정지+승인)·도구별 오늘소비
  미터+**재개 승인** 버튼. build_state에 pace 노출, `/api/config` daily_pace·pace_approve. 라이브 검증(codex
  meter used 0/14%p). 60 passed + 페이싱 테스트 재작성(누적·sticky·real게이트·원장 block).

## 미출시(dev) · 2026-07-13 — 주간쿼터 하루 페이싱(일일 소비 캡) 백엔드 P1~P4+P6 (GUI 제외)

- 주간(7d) 쿼터를 하루 단위로 페이싱: '오늘 소비 = 현재 7d% − 그날 아침 7d% 스냅샷(.yok3x/pace.json)'을
  캡(pct_of_weekly·100 %p)과 비교. soft_frac에서 경고, 캡 도달 시 mode=warn(경고)/pause(정지+승인재개).
- `usage.daily_pace_status`/`pace_approve`/`_weekly_pct` + `check_backend` 병합(절대 5h/7d에 '덧붙는' 층,
  더 빡빡한 쪽 채택, metric=daily_pace). config `guard.daily_pace{enabled,pct_of_weekly,soft_frac,mode,
  backends}` + `daily_pace_override`. CLI `yok3x pace status|approve <backend>`. guiserver `/api/config`
  daily_pace 설정·pace_approve 수용(향후 GUI용). 자정 자동 리셋, override 1일 유효.
- 부수 수정: cli.py profile 분기의 중복 `from . import usage`가 함수 전체에서 usage를 지역변수로 만들어
  pace/limits 분기에서 UnboundLocalError 유발 → 제거(잠재버그 해소).
- opt-in(기본 off), 라이브 7d 있는 backend(claude/codex) 대상. **GUI(P5)는 별도 승인**(§5.6). 신규 테스트 3.

## 미출시(dev) · 2026-07-13 — effort 설정 UI(에이전트 보드) + 보드 local backend (§5.6 승인)

- 사용자 승인(option 1)으로 **에이전트 배치 보드에 워커별 effort 드롭다운** 추가(기본/low/medium/high).
  claude/codex는 활성, gemini/local은 dim(미적용 표시). 저장은 `/api/config` worker_efforts →
  `_apply_config`(값검증 low/medium/high) → workers[w].effort. build_state에 effort 노출.
- 보드 backend 목록에 `local` 추가(P3 워커 local-main이 'claude'로 오표시되던 것 정정).
- gemini는 thinking 지원하나 settings.json(thinkingLevel) 경유라 per-call 미연결 → dim 처리(정직).
  라이브 검증: 드롭다운 렌더 5행, 저장 라운드트립 ok(claude-main=high 반영), JS 에러 0.

## 미출시(dev) · 2026-07-13 — gemini-3.5 가짜 라벨 제거 + 추론강도(effort) 백엔드 통과

- `models_catalog`/profiles/benchmarks의 논리라벨 **`gemini-3.5`(실존 안 하는 이름)를 `gemini-3-flash`**
  로 정정(실제 모델 gemini-3-flash-preview에 매핑). 드롭다운 목록은 원래 실제 CLI 레지스트리라 3.5가
  안 뜨는 게 정상 — 오해를 부른 건 이 하드코딩 라벨이었음(§5.5).
- **추론 강도(effort) 지원**: 워커별 `effort`(low/medium/high) 또는 전역 `default_effort` → backend별
  `effort_arg`로 전달. claude=`--effort <level>`, codex=`-c model_reasoning_effort=<level>`, gemini=미지원.
  `run_backend(..., effort=)` + `_run_cli`. 미지정 시 미부착(기존 동작 불변). GUI 컨트롤은 §5.6로 별도.

## 미출시(dev) · 2026-07-13 — P3 오프라인 폴백(로컬 모델, 의존성 0) — 클라우드 전멸 시 무중단

- 적응형 열화 3단계 신설: 클라우드 백엔드가 전부 stop이면 **로컬 OpenAI 호환 서버로 강등**해 멈추지
  않게. `backends.local`(type openai_http, urllib만 — 의존성 0), `_run_openai_http`(/v1/chat/completions,
  <think> 제거, cost 0), `usage.failover_backend`에 P3 티어 + `offline_reachable`(로컬 서버 있을 때만
  발동), config `guard.degrade.offline_enabled/offline_backend`, worker `local-main`.
- **Ollama 불필요**: 이미 기동된 localhost:8000(llama.cpp+Qwen3.5-4B, id gemma-4-e4b)을 그대로 사용.
  라이브: `run_backend(local)`→`lambda s: s==s[::-1]`(cost0), `list_models(local)`=['gemma-4-e4b'],
  오케스트레이터 local 라우팅 확인. GUI 미노출(BACKEND_KEYS 고정, §5.6). 51 passed. §5.5 A8 등록.

## 미출시(dev) · 2026-07-13 — gemini 모델 목록 드디어 조회(CLI 번들 GEMINI_MODELS 레지스트리)

- gemini는 이 계정에서 Antigravity/CloudSDK **암호화 OAuth**로 인증(env·.env·평문토큰 전무) → 키
  접근 불가라 Google API를 못 쓴다. 대신 **gemini CLI 번들의 `GEMINI_MODELS` Set**을 파싱해 10개
  실제 모델(gemini-3-pro-preview·3.1-pro/flash·2.5-pro/flash/flash-lite·gemma…) 조회 성공.
  codex `models_cache.json`과 동급의 실제 소스(CLI 버전 따라 갱신). API 키 주면 실시간 API가 우선.
- `_gemini_api_key`에 gemini의 `.env` 탐색 미러(cwd→상위→홈) 추가. §5.5 레지스트리 B항 갱신. 49 passed.

## 미출시(dev) · 2026-07-13 — §5.5 폴백·하드코딩 감사 레지스트리 신설(밀린 리포트 보강)

- RULE §5.5가 참조하나 부재했던 **폴백/하드코딩 레지스트리**를 신설:
  `reports/v3.4.0-fallback-hardcoding-audit-2026-07-13.md`. 사용량 실측 폴백 체인(stale-while-error·
  1003% 차단·false-stop 방지 등 최근 3건 포함)·모델목록 폴백·허용 하드코딩을 한 곳에 등록. 위반 0.

## 미출시(dev) · 2026-07-13 — claude 실측 배지 깜빡임 제거(stale-while-error) · critic 텍스트전용 가드

- claude 실측(OAuth usage)이 429/토큰만료로 일시 실패할 때 원장으로 떨어져 배지가 실측↔원장으로
  깜빡이던 문제. `_probe_claude_oauth`에 **stale-while-error** 추가: 최근 실측을 `max_stale_sec`(기본
  900s) 동안 유지하고 detail에 `⚠N분 전 실측·사유` 표시(§5.5 명시적 열화). 지나면 추정→원장.
- producer-reviewer의 **critic/review 프롬프트에도 '파일 만들지 말고 텍스트로만' 가드 추가** —
  없어서 codex 크리틱이 파일생성을 시도하다 "쓰기 권한 대기"만 반복하던 실패모드(is_palindrome 런) 해소.
- gemini: `gemini -p` 정상 동작 확인 → **인증됨(로그인 문제 아님)**. 키가 OS 암호화 저장이라 yok3x가
  못 읽을 뿐. GEMINI_API_KEY(env) 또는 limits.gemini.api_key_path 주면 실시간 목록. 48 passed.

## 미출시(dev) · 2026-07-13 — gemini 모델 실제 API 조회 · 버전 단일출처 · RULE §5.6

- gemini 모델 목록을 **실제 Google `/v1beta/models` API**로 조회(키 있으면 실시간). 키는 config 주도
  해석(`limits.gemini.api_key`/`api_key_path`/`api_key_env`, 기본 GEMINI_API_KEY). 키 없으면 빈 목록
  +커스텀 입력(명시적 폴백). codex 모델은 기존대로 `~/.codex/models_cache.json` 실제 소스.
- 버전 오표시(3.1) 정정: `config.py`·`build_state`가 `_version.__version__`(3.4.0) **단일 출처**를 씀.
- codex "5h 사라짐"은 회귀 아님 — codex(plus)가 `secondary:null`로 **7d 창 하나만** 반환(BUG-12 정정
  결과 정확 라벨). 리포트: `reports/v3.4.0-models-usage-version-audit-2026-07-13-1445.md`.
- **RULE §5.6 신설**: GUI/UX는 사용자 명령 없이 수정 금지, 필요 시 리포트로 제안. 47 passed.

## 미출시(dev) · 2026-07-13 — claude 429 시 미보정 추정 오표시(1003%) 차단 → 원장 폴백

- live(OAuth)가 HTTP 429(호출 과다)로 실패 → 트랜스크립트 추정 폴백 → 미보정 max5x 추정이
  캐시read까지 세어 5h 1003.6%·7d 778.1% 같은 비현실적 값을 표시하던 버그.
- `_probe_claude_oauth`: 추정 사용률이 200% 초과(미보정 확실)면 표시하지 않고 ok=False로 내려
  원장(sane) 폴백에 맡긴다. 결과 → 429 시 `원장 $0/$5 0%`(1003% 대신). §5.5: 못 미더운 추정 대신
  확실한 원장. 토큰은 유효했고 429는 세션 중 API 과다호출로 인한 일시 현상(자동 회복).
- 회귀 테스트 `test_implausible_estimate_is_dropped_for_ledger` 추가. 46 passed.

## 미출시(dev) · 2026-07-13 — CLI 모델 목록 동적 조회(하드코딩 제거, §5.5)

- 워커 모델 드롭다운을 하드코딩 카탈로그 → **동적 조회**로 교체: claude는 Anthropic
  `/v1/models`(구독 OAuth), codex는 `~/.codex/models_cache.json` slug. gemini는 로컬 목록·키
  접근 불가라 명시적 폴백(커스텀 입력). `limits.list_models`(5분 캐시) + `/api/state`
  backend_models. models_catalog는 프로파일 매핑 전용으로 축소. (사용자 지적: 하드코딩/폴백은
  §5.5 위반 — 실제 CLI 캐시/API가 소스임을 재리서치로 확인)
- 부수: 미보정 추정 false-stop 방지(추정 ratio>300%면 정지 유보).

## v3.4.0 · 2026-07-11 — 릴리스: 폴오버 GUI 토글 + 적응형 열화·상황별 프로파일 완성

- **P2 폴오버 GUI 토글**(작동): 콘솔에 폴오버 on/off 스위치(#swfo) — `/api/config`
  failover_enabled 저장, `/api/state` guard.failover 노출. 지금까진 config로만 껐다 켰던 것을
  GUI 스위치로. off 기본(결과 품질 변동 우려).
- v3.4.0 릴리스: `release/yok3x-v3.4.0.zip` + backup/versions 보존 + 정본 스냅샷.
- 누적 반영: 적응형 열화 P1(다운그레이드)·P2(폴오버) · 상황별 모델 프로파일 S1~S3 ·
  워크스페이스/폴더선택 · 모델별 주간(limits[]) · 프로파일 설명 · 워커 수동모델 · codex 창라벨(BUG-12).

## 미출시(dev) · 2026-07-11 — GUI 4종 개선(폴더선택·모델별주간·프로파일설명·수동모델)

- **워크스페이스 폴더 선택 대화상자**: `/api/pickdir`(서버 로컬 tkinter 서브프로세스로 네이티브
  폴더 선택) + GUI '📁 찾기' 버튼. 경로 타이핑 대신 클릭 선택.
- **claude 모델별 주간한도 표시**: `/api/oauth/usage`의 `limits[].weekly_scoped`(scope.model,
  Fable 등)를 파싱해 `7d·Fable` 창으로 표시(seven_day_opus/sonnet은 계정이 null이라 이 배열이
  실제 소스). 현재 Fable 0%(사용 이력 없음), 쓰면 상승·표시.
- **프로파일 설명·미리보기**: 프로파일 드롭다운에 설명(best=상황별 벤치마크 최고점 등) + 선택한
  프로파일이 상황별로 고르는 모델 미리보기(`/api/state` profile_routes, `resolve_model(profile=)`).
- **워커별 수동 모델**: `workers[].model` 지원 — GUI 배치보드에 워커별 모델 드롭다운(카탈로그 기반).
  call_worker가 base로 사용(프로파일 off일 때 적용, on이면 프로파일이 override). `/api/config`
  worker_models 검증·저장. 테스트 2건(off시 수동·on시 프로파일 우선).
- 검증: pytest 40 passed, JS `node --check` OK, GUI 라이브 4종 DOM 확인.

## 미출시(dev) · 2026-07-11 — codex 한도 창 오라벨 수정(BUG-12)

- codex 실측이 `primary→5h·secondary→7d` 위치 고정 매핑이라, codex가 primary에 7일 창
  (windowDurationMins=10080)을 담으면 **주간을 "5h"로 오라벨**하고 7일 줄이 사라졌다. 창 이름을
  **길이(windowDurationMins)로 유도**(`_window_name`)하도록 app-server·sessions 두 경로 수정.
  리포트 `reports/bugs/BUG-12`. 참고: claude가 원장으로 보이던 건 버그 아님 — OAuth 토큰
  만료(~1시간)로 인한 설계상 폴백, claude 한 번 쓰면 자동 갱신.

## 미출시(dev) · 2026-07-11 — 적응형 열화 P2: 백엔드 폴오버(on/off)

- 한도 도달 시 여유 있는 '다른 도구'로 워커를 임시 전환(정지 대신 계속 진행). **on/off 토글**
  `guard.degrade.failover_enabled`(**기본 off** — 결과물 품질이 달라질 수 있어 opt-in). `failover_ratio`
  ↑ 또는 backend stop에서 발동, `usage.failover_backend`가 설치+여유 backend 중 최소 사용률 선택.
  런당 상한(`max_failovers_per_run`) + sticky(런 내 대체 backend 유지)로 스래싱 방지, `roles_no_failover`
  로 역할 제외. S2의 `backend_available` 재사용. call_worker 가드부를 유효 backend 기준으로 재구성.
- off면 현행 그대로(한도 stop → 루프 정지). 테스트 4건 추가(총 36).

## 문서 · 2026-07-11 — 버그 리포트 아카이브(reports/bugs/)

- 2026-07-10~11 디버깅 세션의 버그 11건을 건별 리포트로 기록(`reports/bugs/BUG-01~11`,
  인덱스 `README.md`). 각 파일: 증상·근본원인·진단·수정·검증·교훈. 최대 건은 BUG-10
  (프로듀서 병목 = Windows .cmd 심의 멀티라인 argv 잘림 → stdin 전달로 근본 해결).

## 릴리스 · 2026-07-11 — v3.3.0 릴리스 + 정본 스냅샷

- `release/yok3x-v3.3.0.zip`(21파일, 내부 `__version__` 3.3.0 검증) 생성 + `backup/versions/`
  보존, `VERSIONS.md` 항목 추가. 이전 `release/yok3x-v3.1.0.zip`은 backup/versions에 이미 보존됨.
- 정본 폴더 스냅샷 `backup/yok3x-v3.3.0-20260711/`(로컬, gitignore) — RULE §8.1(minor 상향 +
  변경 다수 누적) 트리거.
- `work`(23커밋) → `main` 머지: v3.1.0 이후 전 작업 반영.

## v3.3.0 · 2026-07-11 — 상황별 모델 프로파일(S1~S3) + 프로듀서 병목 근본 해결

> 기록 정직성 참고(RULE §7): 아래 v3.3.0·v3.2.0 항목은 7/10~11 작업을 뒤늦게 일괄
> 정리한 것이다(그동안 HISTORY 갱신을 빠뜨림). 상세는 git 이력(684c190..) 참조.

- **프로듀서 병목 근본 해결(가장 큰 건)**: Windows에서 claude/codex/gemini는 npm `.cmd`
  심이라 **멀티라인 인자를 subprocess argv로 넘기면 cmd.exe가 첫 줄바꿈에서 잘라버려**
  워커가 프롬프트 첫 줄만 받고 "작업 없음"으로 실패하던 결정적 버그. → 프롬프트를 **stdin**
  으로 전달(`_run_cli`, 커맨드에서 `{prompt}` 제거). 성공률 0/4 → 4/4, 실제 producer-reviewer
  **SCORE 7.0 통과** 확인. 겸사 코드생성 프롬프트 미니멀화 + role의 '파일 편집' 언어 제거.
- **v3.3 S1 — 상황별 모델 프로파일**: `models_catalog`·`situations`·`profiles`(best/balanced/
  cost/speed)·`active_profile` + `resolve_model` 순수함수 + call_worker 라우팅(P1 배관 재사용)
  + `yok3x profile <mode>` CLI. opt-in(비면 현행). 벤치마크/모델ID는 설정(§5.5). 계획서
  `reports/v3.3.0-plan-situational-model-profiles-*`.
- **v3.3 S2 — 가용성·한도 필터**: `backend_available`(CLI 설치 + 한도 stop 아님) + 상황별
  `benchmarks` 점수로, 프로파일 픽이 불가하면 '다음 순위 가용 모델'로 폴백(reason에 '(폴백)').
  "이론상 최고" → "지금 가능한 최고". `yok3x profile` 미리보기도 가용성 반영.
- **v3.3 S3 — best 자동유도 + GUI**: `best` 프로파일을 `_derive`로 → 상황마다 benchmarks
  최고점 모델을 argmax로 자동 채택(데이터 갱신만으로 최신화). GUI 콘솔에 **프로파일 선택기
  + "왜 이 모델" 라우팅 미리보기**(`/api/state` route_preview, `/api/config` active_profile
  검증·저장). 새 backend(GLM 등)는 설정으로 확장 가능(실 CLI 없어 미검증).
- **gemini `--skip-trust`**: 0.44+가 '신뢰 안 된 디렉터리'에서 거부(exit 55)해 gemini 워커가
  조용히 실패하던 것 해결(codex `--skip-git-repo-check` 격). 실측 파싱·토큰 정상 확인.
- **전역 워크스페이스(기본 workdir)**: GUI '에이전트 배치'에서 지정 → 모든 런 상속. `/api/config`
  검증(존재하는 dir만)·저장, `/api/state` 노출. task workdir가 우선.
- **워커 격리 실행**: workdir 없으면 빈 임시 dir에서 실행 — 워커가 실행 cwd의 레포/git을
  주워 오인하는 오염 방지. **run_id 마이크로초** 추가(초 단위 동시 런 충돌·손상 방지).
- **Zed 연동 계획서**(`reports/v3.4.0-plan-zed-acp-integration-*`) + `feat/zed-acp` 분기 파킹.

## v3.2.0 · 2026-07-10 — 라이브 한도 실측(claude/codex) + 적응형 열화(P1) + 하드닝

- **claude 라이브 실측**: 비공식 `GET /api/oauth/usage`(구독 OAuth 토큰)로 5h/7d 현재 사용률·
  리셋을 실시간 조회(메시지 소비 0) — codex급 실측. 실패 시 트랜스크립트 추정→원장 명시 열화.
  모델별 7d·추가크레딧 surfacing, mat '실측' 라벨 버그 수정. `limits.claude.type=claude_oauth`.
- **codex 0.144 파서 호환**: agent 메시지가 `item.completed`의 `item.type=agent_message`로 옮겨가
  SCORE 미파싱되던 것 수정(신·구 스키마 호환).
- **적응형 열화 P1**: 한도 근처(`downgrade_ratio`)에서 lite 모델로 다운그레이드(`degrade_plan`
  순수함수 + `backends.model_arg` 주입). opt-in, 리뷰어 제외, 명시 로깅. 계획서
  `reports/v3.2.0-plan-adaptive-degradation-*`.
- **CLI 백엔드 stdin 데드락 방지**: 헤드리스 실행 중 대화형 대기로 멈추던 것 → `stdin=DEVNULL`.
- **GUI 한도 임계선(Lovable식)**: 게이지에 warn/stop 세로선 + 창별 색 전환.
- **verify_cmd 전역 상속** + **pyproject 패키징·pytest 회귀 스위트**(dep-0, `yok3x[zed]` 등 extra)
  + 생성 설정(yok3x.json/backends.json/*.bak) gitignore.
- **버그 3종**: 공유 가변 기본값 오염(deepcopy) · 버전 표기 드리프트(단일 출처) · 스톨 감지 신호
  개선(리뷰어 결함 기준). 테스트 29케이스로 회귀 잠금.

## v3.1.0 · 2026-07-05 — 오케스트레이션 콘솔 (태스크 탭 → 콘솔)

- **정적 "태스크" 탭을 실동작 "콘솔" 탭으로 전환** (`gui/index.html`):
  - **작업 채팅** — 목표 입력 → `POST /api/run` 인라인 spec 실행, 스텝이 채팅 카드로 흐름. 기존 편집기 기능 전부 포함(패턴·producer/reviewer·max_rounds·pass_score·반복·등록태스크 실행) + 코딩 게이트(workdir·verify_cmd).
  - **실시간 사용량 스트립** — 입력창 아래, 도구별 5h/7d 게이지(작업 돌수록 증가, `/api/state`).
  - **에이전트 배치 보드** — 역할↔CLI(backend) 드롭다운 + flavor, `POST /api/config`로 `yok3x.json` 저장(검증+`.bak` 백업). 실행 큐 표시.
- **백엔드 확장** (`guiserver.py`): `/api/run` 인라인 spec + 실행 큐(단일 실행, 나머지 대기) + 반복(loop); `/api/config` 워커 backend·routing·flavor 편집(검증: 잘못된 backend/워커/flavor 거부); `build_state`에 workers/routing/flavors/queue 노출.
- **버그 수정**: 완료 시 status.json이 작업 목표(task)를 덮어써 채팅에 run_id만 뜨던 문제 → `orchestrator._save_status`가 `task_desc` 항상 보존.
- 전 기능 Windows 실서버 검증(작업 전송→스텝 스트리밍, 보드 저장→yok3x.json 반영, 큐).

## v3.0.2 · 2026-07-05 — GUI 도움말(사용 설명서) 탭

- GUI에 **도움말 탭** 추가(`gui/index.html`): 빠른시작·명령어표·워크플로우 패턴·한도&가드·코딩 태스크 옵션·워커/안전장치·GUI 사용법. 접이식(`<details>`) 7섹션, 코믹 스타일, 버전 자동 표시. 렌더 검증 완료.

## 유지보수 · 2026-07-05 — 버전 아카이브 보존

- 이전 버전 zip을 삭제하지 않고 `backup/versions/`에 보존: v2.2.0(git 복원)·v2.3.0(폴더백업)·v3.0.1(현행). 각 내부 `__version__` 검증, 무결성 테스트 통과. 매니페스트 `backup/versions/VERSIONS.md`.
- v3.0.0은 별도 커밋/zip 없이 v3.0.1로 덮어써져 정확 복원 불가(정직 표기) — v3.0.1이 v3.0.0+감사수정.
- RULE §8: 이전 릴리스 zip 삭제 금지, 새 릴리스 시 `backup/versions/`로 이관. `.gitignore`가 버전 아카이브만 추적하도록 조정.

## v3.0.1 · 2026-07-04 14:14 — 폴백·하드코딩 감사 + 하드닝

- **RULE 5.5 신설**: 폴백·하드코딩은 꼭 필요한 곳에만, 적용 시 별도 리포트 의무. 레지스트리: `reports/v3.0.1-fallback-hardcoding-audit-*`.
- 위반 수정 4건: guiserver 조용한 예외 삼킴(→ GUI에 "직전: 결과/오류" 표시) · mock 사용량 오귀속 폴백(→ 미확정 집계 제외) · verify 타임아웃 하드코딩(→ task `verify_timeout_sec`) · GUI mock 버전 불일치.
- 품질 개선 3건: today_totals mtime 캐시(GUI 폴링 성능, 정합성 테스트 통과) · 레포 컨텍스트 예산 설정화(`repo_context_max_chars`) · workdir 오타 시 트레이스백 → 깔끔한 aborted 처리.
- README에 코딩 태스크 옵션(workdir/verify_cmd/verify_timeout_sec/context_globs/rubric) 문서화(누락 보완).
- 회귀: 3패턴 + mat/coach/limits 전부 통과.

## v3.0.0 · 2026-07-04 14:03 — yok3x 리브랜딩 + 코딩 게이트 완성

- **리브랜딩 harness → yok3x** (하나도 빠짐없이): 폴더/패키지/런처(`yok3x.py`)/상태디렉터리(`.yok3x/`)/설정(`yok3x.json`)/CLI/GUI 타이틀(`YOK3X`)/기법명/문서 전부. 프로그램 폴더 내 `harness/하네스/HARNESS` 잔여 **0건** 확인. 원본은 `backup/`에 보존.
- **테스트/검증 게이트**(객관): task `verify_cmd` — producer 산출 후 명령(pytest/lint 등) 실제 실행, 결과를 리뷰어에 주입 + **비정상 종료를 하드 실패로**(높은 SCORE도 검증 실패면 통과 불가). 검증: score 9 + verify fail → 미통과 확인.
- **작업 디렉터리** task `workdir`: 워커/검증을 실제 cwd에서 실행(실 CLI 파일 편집 구조). backend에 cwd 전달.
- **레포 컨텍스트 주입** task `context_globs`: 관련 파일을 producer 프롬프트에 주입(글자 제한). 검증됨.
- **rubric 주입** task `rubric`: 채점표 파일을 검수 프롬프트에 통째. 검증됨.
- **스톨 감지**: 점수·이슈 2회 연속 동일 → 조기 종료 + knot 실패 기록. 검증(5라운드 태스크가 2라운드에 종료).
- **GUI 실행 배선**: `POST /api/run` + `/api/tasks` — GUI에서 태스크 실행(단일 실행 락, 백그라운드), 진행/실행상태 표시. 검증됨.
- 버전 2.3 → **3.0**. 전 20개 명령·3패턴·신규기능·GUI 라이브 Windows 검증(무오류, knot lint의 의도된 exit 1 제외).

## v2.3.0 · 2026-07-04 13:36 — 코딩 전환 + 라이브 한도 + GUI 프로토타입

- **GUI 프로토타입** (`yok3x gui`): 브라우저 콘솔. claude 코믹 베이스 + lovable 폴리시(둥근 파스텔 패널·소스배지·상태색). 본문/데이터는 가독성 산세리프, 코드·로그·수치는 코딩 폰트(D2Coding→Consolas 폴백), 제목만 코믹. **CDN 0(오프라인)**. `yok3x/guiserver.py`가 `/api/state`로 실제 limits/coach/runs를 서빙 — 목업이 아니라 **라이브 데이터**.
- **yok3x 기법 주입** (`orchestrator.YOK3X_TECHNIQUE`): 코딩 워커에 계획→구현→자가검증(SELF-CHECK) 구조 강제. 4중 브레이크(단계분할·승인·검증·예산).
- **할루시네이션 방지** (`ANTI_HALLUCINATION`/`REVIEW_GUARD`): 전 워커에 사실성 규칙 주입, 검수 워커는 환각 명시 지적, 체크리스트가 근거 없는 과잉 확신 표현 자동 표시.
- **요금제 프리셋 + 캘리브레이트**: `yok3x plan claude max20x`(근사), `yok3x calibrate claude 7d <실제%>`(현재 롤링 토큰 ÷ 실제% 로 정확 역산). codex는 plan 자동 감지.
- **코딩용 전환**: 워커 역할(codex-critic=코드 리뷰어 등)·라우팅(build/refactor/review/test/design_review)·샘플 태스크(슬러그 함수·LRU 캐시·CSV 파싱)를 코딩 워크플로우로 교체.
- **RULE.md · HISTORY.md** 추가.
- 버전 2.2 → 2.3.

## v2.2.2 · 2026-07-04 09:45 — 라이브 codex 실측(app-server RPC)

- `limits.codex_appserver`: `codex app-server` JSON-RPC(`account/rateLimits/read`)로 **지금 이 순간** 5h/7d `usedPercent`·리셋시각 조회. 세션 파일(stale) 방식을 대체. 라이브 실패 시 파일→원장 폴백.
- 근거: 세션 rollout 파일은 마지막 값이라 시간이 지나면 부정확(stale) — 실측 검증으로 확인(파일 7d 5% vs 라이브 24%). claude/gemini는 라이브 소스 부재 확인(추정/원장 유지).

## v2.2.1 · 2026-07-04 05:37 — 진짜 한도 조회 + Windows 수정

- `limits.py` 신설: codex_sessions(파일 실측)·claude_transcripts(롤링 추정)·command(ccusage/tokscale)·ledger. 가드가 실측 우선 + `on_probe_failure` 폴백 정책.
- coach 5h/7d 이중 윈도우 코칭, mat에 소스배지 표시.
- **Windows 수정**: 설정·태스크·노트 읽기 `utf-8-sig`(BOM 크래시 제거), CLI `.cmd` 심 해석(`shutil.which`), stdout/stderr UTF-8 재구성(cp949 콘솔 게이지 깨짐 방지), BrokenPipe 방어.

## v2.2.0 · 2026-07-04 — 초기 구현 (원 매뉴얼 v2.2 기반)

- 요금 가드(루프 자동 정지)·coach·mat·producer-reviewer 교차검증·knot 지식그물·flavor 3종·backends 어댑터(cli/native/mcp/mock)·승인 게이트·파일 로그·context/brief 글자 제한·Fan-out/Pipeline/Producer-Reviewer 패턴.
