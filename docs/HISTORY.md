# HISTORY.md — 변경 이력

형식: `버전 · 날짜 시간 — 변경 요약`. 갱신할 때마다 맨 위에 새 항목을 추가한다.

---

## 미출시(dev) · 2026-07-26 — R-7(2단계) auto-commit 래칫(격리 브랜치 전용) + BUG-39 로그 인코딩 크래시

- **T-3 결정 구현**: `changes.apply_mode = "review"(기본) | "auto_commit"`. 기본은 **파일게시+사람수락**
    그대로라 동작 변화 없음. auto_commit을 켜면 **검증(verify)이 실제로 통과한 라운드**를 체크포인트로 커밋.
- **안전 설계(되돌리기 어려운 작업이라 보수적으로)**:
    ① **사용자 작업 트리·현재 브랜치는 절대 건드리지 않는다** — 전용 worktree + 전용 브랜치
    `yok3x/run_<run_id>`에만 커밋. **자동 병합·push 없음**(병합은 사람이).
    ② `worktree add **-b**`로 브랜치를 만들어 커밋한다 — detached HEAD면 worktree 제거 후 커밋이 어떤 ref에도
    안 걸려 GC 대상이 된다(작업 유실). 실측으로 **정리 후 커밋 생존** 확인.
    ③ 파일은 기존 `artifacts.plan_files` 검증을 통과한 블록만, 격리 트리 밖 경로는 추가 차단.
    ④ git 없음·비-git이면 사유를 남기고 **review 모드로 자기 비활성화**(매 라운드 재시도 안 함). 실패는 런을 안 깬다.
- 실측(임시 실저장소): r1·r2 체크포인트 커밋, 사용자 트리에 산출물 없음, 미커밋 변경 보존, 현재 브랜치 불변,
    worktree 제거 후에도 브랜치에 커밋 2개 생존.
- **BUG-39(검증 중 발견·수정)**: cp949 콘솔이 로그의 `—`(U+2014)를 못 그려 `_log`가 UnicodeEncodeError를
    던졌고, 그 예외로 **래칫 체크포인트 1개가 실제로 유실**됐다. `_log`가 인코딩 실패 시 낮춰 찍도록 수정
    (파일 로그는 utf-8 원문 보존). `sys` import 누락도 함께 수정 — 로그가 런을 죽이면 안 된다. → BUG-39.
- 검증: 신규 테스트 4(기본 off·격리 브랜치 전용·비-git 자기비활성화·cp949 로그). **344 passed**.

## 미출시(dev) · 2026-07-26 — R-7(1단계) 병렬 워커 git worktree 격리 (계획서 v4.4.0)

- 문제: 병렬 fanout이 **모든 워커를 같은 workdir**에서 실행 — 에이전트 CLI가 실행 cwd를 읽고 임시 파일도
    쓰므로 서로의 중간 상태를 보거나 덮어썼다(사용자 작업 트리도 위험).
- 수정: 신규 `yok3x/worktree.py`(의존성0 — git CLI만). `usable()`이 가능 여부를 **사유와 함께** 판정하고,
    가능하면 워커마다 HEAD의 독립 체크아웃을 만들어 `spec.run_cwd`를 전환. `finally`에서 **중단·실패 경로
    포함 항상 회수**(worktree remove + prune, 실패 시 디렉터리 직접 정리).
- **기본 off(opt-in) — 이유**: worktree는 **HEAD 커밋**을 체크아웃해 **커밋 안 된 변경이 워커에게 안 보인다**.
    이 동작 차이를 조용히 강요하지 않으려 `guard.parallel.worktree_isolation` 기본 false, 켤 때 로그로 고지.
    비-git·커밋없음·git부재·생성실패는 **사유 로그 + 공유 workdir 폴백**(조용한 열화 금지, RULE §5.5).
- 검증: 임시 **실제 git 저장소**로 확인 — 워커A의 덮어쓰기가 워커B·사용자 트리 어디에도 반영되지 않고,
    미커밋 변경은 원본에만 남으며, 정리 후 디렉터리 소멸. 신규 테스트 4. **340 passed**.
- 범위: 이번은 **격리까지**. auto-commit(ratcheting)은 T-3 결정(스위치·기본 사람수락)대로 `changes.apply_mode`
    도입과 함께 2단계로 — **이번 변경에 자동 git 쓰기는 없다.**

## v4.4.0 · 2026-07-26 — 릴리스: 버전 표기 정렬 + 리서치 흡수 묶음(R-1~R-6) 완결

- **버전 정렬(사용자 지시)**: `3.6.0` → **`4.4.0`**. v3.7~v4.4 계획이 이미 코드에 반영됐는데 버전을 올리지
    않아 계획서 번호와 코드 버전이 벌어져 있던 것을 해소. 단일 출처 `yok3x/_version.py`만 수정(pyproject는
    정적 파싱, GUI·CLI·매니페스트는 런타임 재노출이라 하드코딩 없음 — BUG-02 재발 없음).
- **패키징**(RULE §8): 스모크 테스트 통과(`init`·`setup`·`run`(mock, done/exit0)·`limits`·`limits --json`,
    GUI는 세션 중 브라우저 실측) → `release/yok3x-v4.4.0.zip`(122 files·490KB) 생성, zip 내부
    `__version__=4.4.0` 검증. RULE §9 트리거①(minor 이상 상향)에 따라 `backup/versions/`에 보존(해시 일치 확인).
    이전 릴리스 zip(v3.4/3.5/3.6)은 `backup/versions/`의 동일본을 **해시 대조로 확인한 뒤** `release/`에서 정리
    (삭제 아님 — 보존본 존재 확인 후 이동 완료). `VERSIONS.md` 항목 추가·'현재 정본' 표기 이관.
- **이 릴리스의 내용**: v3.6.0 이후 누적분(조건부 라우팅·MCP 워커도구·ACQUIRE·F1 게이트/스테이징/review
    bundle·G-1 재개·knot 그래프) + v4.4.0 리서치 흡수 묶음(R-1·F2-2·R-2·R-6·R-3·R-4) + 페이싱 정확도
    하드닝(BUG-26~38). **336 passed** · 버그리포트 38건.

## 미출시(dev) · 2026-07-26 — v4.4.0 비쿼터분 마무리: 정본 스냅샷 + 문서 정합 점검 (RULE §8.1)

- **스냅샷**(RULE §8.1 트리거 ②: 마지막 스냅샷 v3.4.0 이후 HISTORY 5개 초과 누적) —
    `backup/yok3x-v3.6.0-20260726-1414/`(yok3x·gui·tests·docs·pyproject·yok3x.py·README, 121 files).
    폴더 스냅샷은 로컬 보존(.gitignore `backup/*`), 버전 zip은 릴리스 때 `backup/versions/`에 추적 보존.
- **날짜 정정**: R-2·R-6·R-3·R-4 항목을 2026-07-24로 잘못 적었던 것을 커밋 타임스탬프 기준 **2026-07-26**으로
    수정(HISTORY·v4.4.0 계획서). 세션이 이틀에 걸쳐 이어지며 생긴 오기 — F2-2 이하 항목은 07-24가 맞아 유지.
- **v4.4.0 비쿼터 묶음 완료**: R-1·F2-2·R-2·R-6·R-3·R-4 전부 착지(계획서 §3 "비쿼터 우선" 묶음).
    잔여는 R-5(Tier2 강등)·R-7(다음 마일스톤, T-3 결정 선행). 336 passed · 버그리포트 38건 인덱스 일치.
- **HISTORY 롤링**(RULE §7.3 트리거: 803줄·84KB > 800줄/80KB) — 오래된 39개 엔트리를
    `docs/history/HISTORY-v002.md`(2026-07-16 ~ 07-21)로 아카이브, 활성본은 최신 30개 유지
    (346줄·36KB). 인덱스 `docs/history/README.md` 갱신. 엔트리 합계 69 = 30 + 39로 무손실 확인.
- 버전은 **3.6.0 유지** — RULE §8은 버전 상향을 릴리스(zip) 시점에 묶으므로, 릴리스 여부는 사용자 판단 대기.

## 미출시(dev) · 2026-07-26 — R-4 기계판독 스냅샷(`--json`) + 자동화 종료코드 + provenance enum (계획서 v4.4.0)

- 문제: 자동화(훅/CI)가 사용량을 쓰려면 사람용 출력과 detail 문자열의 '(추정)' 라벨을 파싱해야 했다(취약).
- 수정: ① `limits --json` / `pace --json` — 스키마 태그(`yok3x.limits/1`·`yok3x.pace/1`) + 창 수치
    (used_percent·resets_at·토큰)·level·ratio, pace는 전략·cap·even_cap·forward_daily까지 기계판독으로 노출.
    ② `--exit-code`(**opt-in**) — 0=ok · 3=warn(soft) · 4=stop(hard). 플래그가 없으면 기존대로 항상 0이라
    기존 스크립트 동작은 불변. F2-2의 run 종료코드(0/1/3)와 규약을 공유.
    ③ provenance를 **enum**으로 승격: `LimitReading.provenance()` → measured/estimated/ledger/unavailable.
    source(원천 채널)는 유지하고 '얼마나 믿을 수 있나'를 별도 축으로 분리(리포트 10 — 제거가 아니라 계약 승격).
- 검증: 신규 테스트 11(enum 매핑 7분기·JSON 계약·종료코드 opt-in 3레벨) + 실측(claude provenance=measured,
    gemini=unavailable). 336 passed.

## 미출시(dev) · 2026-07-26 — R-3 preflight 예산 검사: 못 끝낼 런은 시작 전 거부 (계획서 v4.4.0)

- 문제: cost guard가 **반응형**이라 한도에 닿아야 정지 — 예산을 절반 태우고 중단되는 낭비가 났다.
- 수정: ① `reserve.headroom()` — 예약 원장 lock 아래에서 `_hard_limits`+pending을 **재사용**해 지표별 잔여
    (limit−실사용−타런 pending)를 일관 스냅샷으로 반환(codex 조건: preflight가 별도 계산을 두면 예약 경로와
    판정이 어긋남). 상한 0=무제한(inf), 자기 예약은 제외(이중계상 방지), lock 실패 시 None=판단 보류.
    ② `project_run_cost(spec)` — 패턴별 **최악 호출 수**를 spec에서 결정론적으로 산출(producer-reviewer=라운드×2,
    pipeline=스테이지, fanout=워커+join, acquire=질문자1+답변자×qa_count) + 토큰·USD 보수 추정.
    ③ `preflight_budget()` — 초과 예측 시 `RunAborted(cause="budget_preflight")`로 **백엔드 호출 0회에서** 중단,
    `stop_reason=budget_preflight`를 status.json·sink에 기록. `guard.reservation.preflight_enabled=false`로 해제 가능.
- 실제 강제(enforcement)는 기존 배치 `reserve()`의 원자적 예약이 그대로 담당 — preflight는 예측·조기거부 계층.
- 검증: 신규 테스트 4(원장 재사용·패턴별 최악값·거부·통과/해제) + e2e(잔여 2콜에 10콜 런 → 호출 0회로 거부,
    status/sink 모두 budget_preflight). 325 passed.

## 미출시(dev) · 2026-07-26 — R-2 정지규칙 재설계(새 증거 기반) + R-6 verifier 불변성 (계획서 v4.4.0)

- **R-2**: 기존 스톨감지는 `sig=(score, issues_sig)` 문자 비교뿐이라 ① 산출물을 고쳤는데 리뷰어가 같은 말을
    반복하면 스톨로 오판, ② 점수만 1점 흔들리면 진전 없이도 계속 재시도했다. → **새 증거 기반 재시도 게이트**
    (`_new_evidence`): 산출물 내용(`_artifact_sig`, 공백정규화 SHA256)·verifier 상태·지적 결함 집합 중 하나라도
    바뀌면 재시도, 셋 다 불변이면 조기 종료. 더불어 **명시적 `stop_reason` 라벨**(success/no_new_evidence/
    max_rounds/producer_failed)을 status.json과 F2-2 sink에 노출 — 자동화가 정지 원인을 문자열 파싱 없이 소비.
- **R-6**: 프로듀서가 검증기 자체(테스트·CI 설정)를 고쳐 게이트를 우회하는 실패모드 차단.
    `PROTECTED_VERIFIER_GLOBS`(tests/**·**/test_*.py·*_test.go·conftest.py·pytest.ini·Makefile·.github/workflows/**)에
    걸리는 후보는 스테이징에 적용하지 않고 **fail-closed**(라운드 후보 전체 거부 → 원본 트리 verify로 열화,
    candidate 라벨 안 붙어 T-1 지상진실 오염 없음). `changes.protected_globs`로 재정의 가능.
    R-2만 있고 R-6가 없으면 재시도를 통제해도 verifier를 바꿔 우회 가능 — 그래서 함께 착지(codex 지적).
- 검증: 신규 테스트 7(증거 축 5분기·산출물서명 정규화·stop_reason 3라벨·보호경로 매칭·재정의·후보거부 e2e).
    321 passed. 테스트가 실제 버그 1건 발견(`lstrip("./")`가 `.github`의 앞 점을 벗겨 보호 패턴 회피) → 수정.

## 미출시(dev) · 2026-07-24 — F2-2 gate.passed 소비자 전환: run 종료코드에서 실행상태·산출물승인 분리 (R-2 선행계약)

- 문제: CLI `run`이 `state=="done"`이면 무조건 exit0 — strict 게이트 저점 탈락(done+gate.passed=false)도 성공으로
    소비돼, 곧 할 R-2(verifier-gated 정지)의 의미가 종료코드에서 소실될 상황(codex 선행계약 지적).
- 변경: `run_task_file(sink=...)`가 run_id·gate를 sink에 채우고(반환 문자열 계약은 불변 — 기존 테스트/호출자
    영향 없음), CLI가 종료코드를 **1(실행 중단/실패) / 3(done이나 gate.passed=false=산출물 미승인) / 0(완료+승인)**
    로 분리. state=실행 생명주기, gate.passed=산출물 승인.
- 검증: 신규 테스트 2(run_task_file sink에 run_id·gate 채움 / CLI 종료코드 3분기). 314 passed.
    v4.3.0 F2-2 체크·v4.4.0 R-2 선행계약 해제(R-2 착수 가능) 반영.

## 미출시(dev) · 2026-07-24 — GUI 저장 안 됨 원인: 트랜스크립트 전량 재스캔으로 build_state 13초 (BUG-38, 사용자 지적)

- 사용자: 가드 경고 84%로 바꿨는데 저장이 안 됨. 조사: `/api/config` POST는 즉시 성공(디스크 0.84 반영)이나
    `/api/state`가 >30초 타임아웃 → 저장 후 load()가 멎어 화면 갱신 안 됨 → '저장 실패'처럼 보임.
- 원인: `_rolling_claude_tokens`가 호출마다 트랜스크립트 전 *.jsonl을 read+json.loads. build_state 한 번이
    5h·7d·이번주·오늘·autocalibrate로 4~6회 전량 재스캔(BUG-37의 일간 스캔 2건이 임계 넘김) → 13초.
- 수정: 파싱 결과를 파일당 캐시(`_TRANSCRIPT_EVENT_CACHE`, mtime·size 키). append-only라 불변이면 재사용,
    창 질의는 메모리 이벤트를 cutoff 필터만. build_state 13초 → 콜드 6초·웜 0.4초. 폴링 적체·저장 지연 해소.
- 검증: build_state 웜 0.4s, `/api/state` 이후 0.5s, guard.soft=0.84가 API·GUI 슬라이더(84)에 반영. 312 passed. → BUG-38.

## 미출시(dev) · 2026-07-24 — 오늘 0 붕괴·상한 드리프트 근본수정: 트랜스크립트 환산 일간 페이싱 (BUG-37) + %p→% (사용자 지적)

- 사용자: claude 오늘 실제로 썼는데 `오늘 0%`, 상한이 `11→10.7→10.3` 실시간 하락. "이러면 안 되는 거 아냐?
    그리고 왜 %p라고 해?" 원인(BUG-37): OAuth 스냅샷 모델(`오늘=현재−하루시작값`)이 프로세스 재기동·소스
    플립 때 하루시작값(start_pct)을 현재값으로 재캡처 → 오늘=0 붕괴, 상한(=start_pct 기반)이 start_pct 상승 따라 드리프트.
- 수정: claude는 **트랜스크립트(과거 토큰, 재기동 불변)** 의 오늘/이번주 비율로 OAuth 주간 총량을 '오늘분'으로
    **환산**해 since_reset_known=True 경로로 넘긴다 → u0=현재−오늘(=오늘이전, 하루 고정)로 상한 안정, 오늘=실제 사용.
    스케일을 OAuth에 맞춰(BUG-33 무보정 혼합과 달리) 요동 없음. 트랜스크립트 없으면 스냅샷 폴백.
- 표시: 페이싱 대시보드·오버레이·미니의 `%p`→`%`(사용자 요청). 설정 패널의 개념 설명 라벨은 유지.
- 검증: 신규 테스트 2(환산 정확 7.1×69/53.9≈9.1 + 사용 늘어도 오늘이전 앵커 불변). 브라우저 실측:
    `오늘 0% / 상한 10.3%(드리프트)` → **`오늘 9.9% / 상한 13.3%(안정)`**. 312 passed. → BUG-37, README.
- codex 한계(별개): 세션 rate_limits %창만 있고 토큰 일간 데이터 없어 같은 환산 불가 — 정수 스냅샷 유지(후속 과제).

## 미출시(dev) · 2026-07-24 — 하루 상한 전략 spread 전환 + 엄격 균등값 오버레이 분리 (사용자 설계 ②)

- 사용자 설계: 메인 줄 상한은 **지속가능률(남은예산÷남은일수, 하루 단위 갱신)** 로 안정되게, **쓴 만큼
  줄어드는 엄격 균등선(catch_up) 값은 오버레이에만**. 기존엔 catch_up 상한(예: 67%면 3%)이 메인 줄에
  떠서 "상한이 자꾸 준다"는 반복 불만(BUG-26 이래) 유발.
- 변경: ① 전략을 `catch_up`→`spread`(guard.daily_pace.strategy). claude(OAuth 스냅샷)·codex는 하루 상한이
  `(100−주간%)÷남은일수`로 하루 시작에 고정(예: 주간 68%·2일 → 11%). 하루 안에서 안 줄고 6시 경계에만 갱신.
  ② `daily_pace_status`가 전략과 무관하게 `even_cap`(엄격 catch_up 여유)을 함께 반환 → guiserver가 노출 →
  GUI 오버레이에 "엄격 균등선 기준으론 오늘 N%p(쓴 만큼 줄어듦)"로만 표시(메인 줄은 안정 유지).
- 트레이드오프(사용자 수용): spread는 초과 시 catch_up처럼 급조이지 않고 남은 날에 폄. 가드 보호는 유지
  (오늘 사용이 상한 넘으면 여전히 경고/정지). 전략 버튼(fixed/catch_up/spread)은 그대로라 언제든 되돌림 가능.
- 검증: 신규 테스트 `test_spread_main_cap_stable_even_cap_carries_shrinking`(spread=11·even_cap=3·하루고정).
  브라우저 실측: claude `오늘 1%p / 상한 11%p(균등 14%p)`, 오버레이 "…11%p까지 여유 · 엄격 균등선 기준으론
  오늘 3%p(쓴 만큼 줄어듦)". codex `상한 5.2%p`·오버레이 0%p. 311 passed. (BUG-36 oauth_live.json 실측 영속화도 확인.)

## 미출시(dev) · 2026-07-24 — 페이싱 안정화 2건: 캘리브 스윙 클램프 + OAuth 실측 디스크 영속화 (BUG-35·36, 사용자 지적)

- **BUG-35(캘리브레이션 스윙)**: 5h/7d 상한이 하루 중 246M→423M→793M(3배)로 널뜀. 원인: autocalibrate가
  `cap=tokens/(pct/100)`를 사실상 무제한(0.001x~1000x)으로 그대로 저장 — 창이 작아 %가 흔들리는 5h에서
  노이즈가 cap에 증폭. 수정(끄지 않고 고침, 사용자 요청): 회당 변화를 ±`calib_max_step`(25%)로 **클램프**,
  단 **이미 보정된 값(override>0)에만** 적용(첫 보정은 preset→현실 즉시 스냅, 이후만 안정화). 정확도 유지·
  수렴 보장·스윙 소멸.
- **BUG-36(소스 플립)**: claude '오늘 소비'가 프로세스 재기동·CLI 호출마다 실측↔트랜스크립트(0)로 깜빡임.
  원인: stale-while-error가 쓰는 `_OAUTH_LIVE_CACHE`가 프로세스별 인메모리라 새 프로세스에선 소실 →
  트랜스크립트 폴백. 수정: 성공 실측을 `.yok3x/oauth_live.json`에 **원자적 영속화**, 새 프로세스는
  디스크 실측을 이어받아 stale-while-error(1h)가 프로세스 경계를 넘음(CLI/GUI 공유·429여도 66% 유지).
- 사용자 결정 반영: "끄기/억제 말고 고쳐서 정확하게"(1번=클램프) + "영속화는 지장 없으면 해"(2번).
- 검증: 신규 테스트 2(클램프 수렴·스윙방지 / 디스크 영속화 프로세스 경계). 310 passed. → BUG-35·36, README.

## 미출시(dev) · 2026-07-24 — 오늘 소비 0 리셋 버그: 하루키 분단위 양자화 (BUG-34, 사용자 지적)

- 사용자: claude 오늘 소비가 1.6→1.8까지 오르다 0으로 돌아감(반복). claude.ai 주간 66%=OAuth 일치라
  정확도 문제 아님. 원인: OAuth resets_at이 정확히 .5초 경계(...400.501429)라 폴마다 초이하 드리프트
  (.41↔.50)에 `round()`가 ...400↔...401로 튀어 → win_gen/하루키 변경 → spurious 하루 리셋 →
  스냅샷 모델(BUG-33)의 start_pct가 현재값 재캡처 → used_today=0 초기화.
- 수정: `_pacing_day_key`·win_gen을 초단위 round() → **분단위 양자화(//60)**. 초이하 드리프트 완전 면역,
  진짜 주간 리셋은 여전히 감지. 오염된 live 레코드는 start_pct=OAuth현재−transcript오늘추정으로 복구.
- 검증: 신규 테스트 2(.0~.99 지터에 키 불변·used_today 누적 유지). 실측 `pace` 2회 `claude 오늘 3/23%p`
  안정. 308 passed. → BUG-34, README.

## 미출시(dev) · 2026-07-23 — 페이싱 상한 요동 버그 수정: 소스 일관성 (BUG-33, 사용자 지적)

- 사용자: "왜 상한이 올라감? 이거 그냥 버그" — claude 상한이 하루 중 8.8→6.6→6.8로 요동, 오늘 쓸수록 상한↑.
- 원인(BUG-29 부작용): 상한 앵커 `current−today_used`에서 current=OAuth(정체), today_used=트랜스크립트(증가)로
  소스가 달라, today가 자랄수록 빼는 값↓→상한↑. 같은 소스여야 상쇄돼 안정적인데 섞였다.
- 수정: `_pace_inputs` 헬퍼(3곳 공유)로 소스 강제 일관 — OAuth 7d%면 codex처럼 스냅샷 모델(since_reset_known
  =False·today_used=None, 상한 하루 고정), 트랜스크립트면 same-source token today(True). 바-밴드 정렬(BUG-29)은 유지.
- 검증: 신규 테스트 2(OAuth→(False,None); 상한 65→66→68%로 성장해도 고정, 오늘만 증가). 306 passed.
- 미해결(별개): 상한 값이 낮은(≈5%) 건 OAuth 65% vs 트랜스크립트 48% 차이 — claude.ai 실제 %로 정확도 확인 필요.

## 미출시(dev) · 2026-07-23 — 초과 상한 형식 '(균등 14%p)' 통일 + 툴팁 실제값화 (사용자 요청)

- ① 초과 시 상한을 '(초과)' 대신 '(균등 14%p)'로 — 비초과 케이스와 동일 형식(상한값은 여전히
  forward_daily). 초과 여부는 warn 색상+툴팁이 전달. ② 툴팁을 안내문구→실제값: "이번주 X% 소비 ·
  오늘 Y%p 소비 · (초과 시)리셋까지 하루 ~Z%씩 지속가능 / (정상)오늘 상한까지 여유". 토큰 소모량은
  있을 때만(live % 모드=OAuth/app-server는 토큰 없음 → 그 사실 명시; 추정 모드에서만 토큰 표시).
- GUI 렌더만 변경. 실측: codex "상한 5.6%p(균등 14%p)"+툴팁 "이번주 72%·초과→하루 5.6%", claude 정상.

## 미출시(dev) · 2026-07-23 — 초과 시 상한 칸에 지속가능률 표시 (codex 등 초과 상태, 사용자 요청)

- 사용자: 초과 시 "상한 0%(초과)" 대신 "상한 8.2%"(초과분 반영 지속가능률)로 나와야. 0%는 '오늘 더 쓸
  여력'으론 맞지만 무의미하니, 상한 칸에 forward_daily(=남은예산÷남은일수 균등배분)를 표시.
- GUI 렌더링만 변경(daily_pace_status 데이터·가드 로직 불변 — 가드는 여전히 cap=0으로 정지 판정). 초과
  분기에서 capTxt=`상한 ${fd}%p(초과)`, 중복되던 fdTxt 제거. 비초과(claude 등)는 else 브랜치라 완전 불변.
- 실측: codex `오늘 0%p / 상한 8.2%p(초과)`, claude `오늘 1%p / 상한 9%p(균등 14%p)`(불변). 콘솔 에러 없음.

## 미출시(dev) · 2026-07-23 — GUI 페이싱 표시 "상한 0%·이후 하루 X%" 혼동 해소 (사용자 지적)

- 사용자: "이후 하루 7.2%로 나오는데 왜 오늘 상한은 0%로 나와?" — 서로 다른 두 지표(오늘 몰아쓸 여력=0
  vs 남은기간 균등배분시 지속가능률)가 연관 설명 없이 나란히 떠 모순처럼 보였다. 값 자체는 정상
  (catch_up: 2일차 허용28%인데 이미 57%소비 → 오늘 추가분 0으로 clamp; forward_daily는 별개 안내).
  버그 아님을 실측 확인(라이브 reset_at·롤링% 검증) 후 표시만 명확화.
- `forward_daily!=null`(=오늘 사용≥상한, 초과 상태)를 `over` 플래그로 재사용해 capTxt에 '(초과)'를 붙이고
  fdTxt를 '이후 하루' → '초과분 반영 시 이후 하루'로 변경 — 두 숫자가 같은 상황의 다른 면임을 명시.
  실측: "오늘 1%p / 상한 0%p(균등 14%p·초과) · 초과분 반영 시 이후 하루 ~7% · 리셋 5일 1시간 후".
  정상(비초과) 케이스는 문구 불변 확인.

## 미출시(dev) · 2026-07-23 — BUG-32 save_yok3x 원자적 쓰기 + Config.load 손상파일 방어 (GUI 기동 크래시)

- GUI 프리뷰 기동이 `yok3x.json`(0바이트) JSONDecodeError로 크래시. 원인: `save_yok3x`가 `write_text`
  직접 사용(truncate-then-write) — 이 세션에서 GUI 프로세스를 코드반영차 여러 번 강제종료했는데, 그
  순간이 저장 중이었을 가능성. `_save_pace` 등엔 이미 있던 원자적 패턴이 `save_yok3x`만 빠진 불일치.
- 수정: `save_yok3x`를 pid 고유 임시파일+`replace`로 원자화. `Config.load`도 방어적으로 — 손상 파일이면
  크래시 대신 기본값 폴백+`logging.warning`(조용히 안 삼킴). `_load_json_or_empty` 헬퍼(yok3x·backends 공용).
- 손상된 live config를 `.bak`(3일 전)에서 복구 후 오늘 세션 값(calibrate 5h/7d 토큰·max_stale=3600·
  auto_refresh=False) 재적용. 신규 테스트 4(손상폴백·경고로그·깨진JSON·원자성 시뮬레이션). 304 passed.
- → BUG-32, README 색인.

## 미출시(dev) · 2026-07-23 — F2-1/R-1 테스트 격리 안전장치 (최우선 안전항목, Claude 구현)

- 배경: mock 무력화(config 오설정·새 테스트 배선 누락) 시 테스트가 실제 claude/codex/gemini를 호출해
  최대 수백초 행+실쿼터 소비 위험(N0'서 실제 발생). 이전에 여러 번 착수를 미루고 페이싱/GUI로 새던 항목.
- 1차 시도(전역 subprocess.run/Popen/urlopen 차단)는 **verify_cmd**(orchestrator가 사용자 작업의 테스트를
  실제 실행하는 기능, 항상 real·안전·비용 없음)와 프로세스종료 테스트까지 막아 5개 테스트 회귀 — 같은
  `subprocess` 모듈 객체를 orchestrator.py도 쓰기 때문. 근본 원인 재분석 후 **명령 인식형** 차단으로 교체.
- `tests/conftest.py`(신규): autouse fixture 이중 안전장치. ① `live` 마커 없으면 **실행파일명이
  claude/codex/gemini일 때만** subprocess.run/Popen 차단(`usage.BACKEND_KEYS` 기준, npm `.cmd` 심 해석된
  전체경로도 베이스이름으로 정확 매치·BUG-10/18 대응), urlopen은 예외없이 차단(real 호출이 정당한 테스트
  없음). verify_cmd(`python task.py` 등)·비-backend 로컬 실행은 통과. ② `live` 마커 있어도
  `YOK3X_ALLOW_LIVE=1` 없으면 스킵. 기존 테스트의 자체 monkeypatch(subprocess/urlopen)는 같은 모듈 객체를
  나중에 setattr하므로 정상적으로 이 차단을 덮어씀(오버라이드 확인).
- 신규 테스트 8(`test_safety_net.py`): 명령명 파싱·차단 3종(claude/codex/gemini)·통과 1종(로컬 파이썬)·
  urlopen 차단·기존 monkeypatch 오버라이드·live 마커 스킵(env 없이 실행하면 도달 못 하는 pytest.fail 캐너리).
  `YOK3X_ALLOW_LIVE=1`로 직접 실행해 스킵 해제도 검증. 302 items(301 passed+1 skipped). → F2-1/R-1 체크.

- 앞선 %통일 커밋에서 title 속성+점선밑줄(CSS `cursor:help`)까지 추가했는데도 "아무것도 안 뜬다"는 재지적.
  원인: 네이티브 `title` 툴팁은 OS/브라우저 크롬이 그리는 오버레이라, 일부 렌더러(프리뷰 등)가 이를
  전혀 그리지 않는다(내 스크린샷 캡처도 동일 렌더러 한계로 지속 타임아웃 — 같은 근본원인 방증).
- 수정: `title=` 대신 `data-tip=` 속성 + CSS `[data-tip]:hover::after{content:attr(data-tip)}`로 툴팁을
  **페이지 페인트 레이어에 직접** 그림(OS 오버레이 아님) → 어떤 렌더러에서도 보장. 적용 지점: 상단 요약
  (`.plan.mono`)·창별 리셋줄(`.rst`) 둘 다. 값은 esc()로 이스케이프(XSS 방지, 기존 패턴 유지).
- 검증: 서버가 새 data-tip 속성·CSS를 정확히 서빙함을 curl로 확인, JS로 `[data-tip]` 매치·`cursor:help`
  적용 확인. 실제 :hover 트리거 스크린샷은 도구 장애로 불가(코드 정확성은 속성값·CSS 계산으로 확정).

## 미출시(dev) · 2026-07-22 — GUI 사용량 표시 %로 통일, 토큰량은 호버로 (사용자 명령, Claude 구현)

- 상단 요약(`.plan.mono`)이 원본 detail(토큰 raw dump)을 42자에서 잘라 흉하게 끊겼다("...7d 5,568,26").
  창별 줄도 "사용 X / 남은 Y" 토큰텍스트가 본문에 상시 노출. 사용자: "% 로 통일하자 토큰량은 오버레이(호버)".
- 상단: `pctSummary`(창별 `이름 %`, 예 "5h 97% · 7d 48%")를 본문에, 원본 detail은 `title` 속성(호버)으로.
  창별 `rst` span: 토큰텍스트(`tok`)를 본문에서 제거하고 title(페이싱 span은 기존 설명에 이어붙임)로 이동.
- 검증: 브라우저에서 claude "5h 97% · 7d 48% · 7d·Fable 20%"(본문) + 호버 시 전체 detail·토큰 확인,
  gemini도 동일 패턴("0%" 본문/"0/2,000,000 tok" 호버). 레이아웃 정상.

## 미출시(dev) · 2026-07-22 — A+B 계층형 claude 사용량: OAuth 재활성(백오프) + 주간 위상 폴백 (codex 공동설계, 사용자 요청)

- 배경 재조사(웹): OAuth usage 429는 알려진 **버그**(#31637)지 밴 아님. 4-4 서드파티 차단은 **6-16 철회**
  (Anthropic: Agent SDK·claude -p·서드파티 구독 사용 그대로). → R-01의 밴 위험 판단 과했음. statusLine은
  이 SDK 환경에서 안 뜸(TTY 없음). rate_limits의 다른 프로그램 경로 없음(트랜스크립트·헤더 미저장 확인).
- codex A+B 채택: **OAuth(저위험) 우선 → 실패 시 백오프 → transcripts+주간위상 폴백**.
  - OAuth 재활성(`claude_oauth`) + **실패 백오프**: 429/네트워크 지수(최대 30분), 401/403 장기중단(재시도 무의미),
    성공 시 해제. auto_refresh off 유지(client_id 사칭 없음). 실측 성공 시 5h/7d + 실제 리셋시각.
  - **주간 위상**: 실측 성공 시 7d 리셋을 `weekly_reset_epoch`에 저장 → transcripts 폴백이 주 단위로 전개해
    7d 창에 실제 리셋 카운트다운 부착("168시간 롤링" 해소). 7d는 고정 주간이라 위상 하나로 안정. 5h는 세션기반→롤링.
  - 정직: 실측을 얻은 적 없으면 리셋 지어내지 않음. 오염된 pace 캐시(9999999999) 정정.
- 실측: OAuth 성공 시 5h 66%/7d 45%/Fable 15% 실측+리셋; 백오프 시 transcripts 7d "Sun 07-26 18:00(4일 후)".
  신규 테스트 3(위상 저장·transcripts 위상 적용·백오프). 293 passed. (기본값은 보수적 claude_statusline 유지.)

## 미출시(dev) · 2026-07-22 — F-08 statusline 라이브 사용량(R-01b, 사용자 요청, Claude 구현)

- R-01(OAuth 폐기)의 안전한 대체재. Claude Code가 statusLine 명령에 **stdin으로** 주는 rate_limits를
  수동 소비 — OAuth 토큰·Anthropic API·client_id 사칭 전무(1st-party 출력만, codex 권고 경로).
- 스키마 권위 확인(guide 에이전트, code.claude.com/docs): rate_limits.five_hour/seven_day.{used_percentage,
  resets_at(epoch초)}. Pro/Max·세션 첫 응답 후에만·각 창 독립 누락·API/enterprise 부재 → 없음/만료 시 추정 폴백.
- 구현: `yok3x statusline` 핸들러(stdin→~/.yok3x/statusline.json 캐시+상태줄 출력, cwd 무관·경량) +
  `claude_statusline` 프로브(real=True, 리셋시각 포함) + 기본 타입 전환. E2E: claude 실측 5h 92%/7d 38%,
  **"168시간 롤링"→실제 리셋시각 복귀**. 신규 테스트 4(캡처·폴백·만료·깨진 JSON). 289 passed. → docs/statusline-setup.md.

## 미출시(dev) · 2026-07-22 — claude OAuth 사용량 프로브 중단(정책 차단·계정 안전) (BUG-27·R-01, 사용자 요청)

- 외부 리서치 R-01 지목 → 코드·웹 교차검증: claude 프로브가 `type=claude_oauth`+`auto_refresh`로
  `api.anthropic.com/api/oauth/usage`를 구독 OAuth 토큰으로 60초마다 호출(429). **Anthropic 2026-04-04 정책**이
  서드파티의 구독 OAuth 사용을 차단한 그 경로. (워커 디스패치=공식 CLI 서브프로세스는 1st-party라 안전·불변.)
- 수정: claude 프로브를 로컬 트랜스크립트 추정(`claude_transcripts`)으로 전환+auto_refresh off. config.py 기본값·
  live yok3x.json 모두. 라이브는 이미 429로 죽어 기능 손실 0, 계정 리스크만 제거. 285 passed. GUI 재기동.
- 후속(R-01b): 라이브가 필요하면 F-08(Claude Code statusline stdin JSON, 1st-party) 사용. → BUG-27, README 색인.

## 미출시(dev) · 2026-07-22 — claude 상한 앵커에서 '오늘 사용' 제외(하루 안 안정) (BUG-26 후속, 사용자 지적)

- claude "오늘 1%/상한 23.5%"가 오늘 쓸수록 상한이 계속 바뀜. 원인: 상한 앵커 u0=since_reset(오늘 포함)라
  오늘 쓸수록 상한=k·q−u0가 깎임. 수정: claude(토큰)는 앵커를 current−today_used(오늘 제외)로 매 폴 재계산 —
  과거 데이터라 하루 안 안정, 오늘 사용이 상한을 안 깎음. 실측 24.6 불변(오늘 1.1→3.0에도).
- 표시 수치가 사용자 기대(4.4%/28%)와 다른 건 claude가 real=False 전사 추정이기 때문 — 별개, calibrate 권장.
- 신규 테스트 1(오늘 늘려도 상한 불변). 182 passed. GUI 서버 실행 중이면 재시작해야 반영.

## 미출시(dev) · 2026-07-22 — codex '오늘' 첫날 리셋 기준 표시 (BUG-26 후속, 사용자 지적, Claude 구현)

- 상한(week_used)만 고쳤더니 codex "오늘"이 0으로 표시(리셋 직후 5% 썼는데 0/9). 원인: 오늘 사용 기준선을
  current로 잡아 관측 시작 시 이미 있던 5%를 '오늘 이전'으로 치부. codex 리셋은 당일 11:01이라 그 5%는 전부 오늘.
- 수정: week_used 누적을 **하루 시작 스냅샷(start_pct)** 모델로 정리. `is_day1`(하루 시작==마지막 리셋)이면
  start_pct=0 → 오늘=현재 롤링%, 상한=기준 온전. 이후 날은 하루 시작 롤링%가 기준선. 오늘=max(0,current−start_pct).
- 실측: `codex 오늘소비 5/14%p`(이전 0/9). 테스트 교체(첫날 5/14, 3일차 0/12, claude 대조 12). 181 passed.

## 미출시(dev) · 2026-07-22 — codex 하루 상한 롤링% 앵커 버그 수정 (BUG-26, 사용자 지적, Claude 구현)

- 사용자 지적: claude는 리셋 기준으로 오늘이 초기화되는데 **codex는 '상한만 줄고 사용량만 늘고'** 리셋 갱신 안 됨.
  실측 `pace.json`: codex `cap_today=10`(=14−롤링4%), claude `cap_today=26.2`(since-reset 15.8 기준).
- 근본원인: codex는 토큰 없어 `weekly_used_since_reset`=None → 호출부가 **7d 롤링 %**를 상한 앵커 `u0`로 넘김.
  롤링 %는 직전 창 사용까지 섞여 서서히 올라 `상한=14−롤링%`가 계속 감소. 창이 자주 리셋돼 k=1 고착(catch-up 미증가).
- 수정: `daily_pace_status`에 **주간 창 이후 누적 증분 `week_used`**(win 변경 시 0 초기화, 아니면 양의 증분 누적)
  + `since_reset_known` 파라미터. False(codex)면 상한 앵커를 롤링%가 아니라 `week_used`로. claude(True) 경로 불변.
  호출부 3곳(cli·guiserver·usage) 배선. 낡은 codex pace 레코드 1회 삭제 → 재초기화. 실측 codex 상한 9→14 교정.
- 검증: 신규 테스트(새 창인데 롤링 30% 남아도 codex 상한 14 vs claude 0). 181 passed. → BUG-26, README 색인.

## 미출시(dev) · 2026-07-21 — 페이싱 상한 누적을 '이번 주(리셋 이후)'로 (사용자 지적 버그, Claude 구현)

- 상한이 0.9%로 나오던 버그. 상한 계산의 누적이 **7d 롤링%**(리셋 전 사용까지 포함)라 과다 차감됐다.
  실측: 롤링 27.2%인데 **리셋 이후 실제는 13.6%**. 리셋 이후로 계산하면 상한 14.8~28%(>14%, 사용자 예상 일치).
- `weekly_used_since_reset(cfg, backend, reset_at)`: claude는 마지막 리셋(reset_at−7일) 이후 실제 토큰으로
  이번 주 사용률 계산. daily_pace_status의 current_pct(상한 누적 기준)로 이 값을 우선 사용, 없으면 7d 롤링 폴백.
  guiserver·cli·usage 콜러 배선. 테스트 1(mock 토큰: since-reset 13.6 vs 롤링 27.2).
- 한계: reset_at 필요(GUI oauth 경로엔 있음, transcript 폴백엔 없어 롤링 폴백). codex는 transcript 없어 롤링 유지. 282 passed.

## 미출시(dev) · 2026-07-21 — 페이싱 하루 경계를 리셋 시각에 정렬 (사용자 지적, Claude 구현)

- 앞선 today_used가 하루 경계를 **자정**으로 잡았는데, 실제 주간 리셋은 자정이 아니다(사용자: 한국 오후 6시).
  → `_pacing_day_start(reset_at)`: reset_at에서 86400초씩 물러난 경계 중 now 직전 = 리셋 시각 정렬 하루 시작
  (리셋 오후6시면 하루도 오후6시~오후6시). `_pacing_day_key`로 레코드 일일 초기화도 그 경계에 정렬.
- today_used_pct·daily_pace_status(today=리셋정렬키)·guiserver·cli 콜러 배선. reset_at 없으면 자정 폴백.
- 검증(합성 리셋 오후6시): 오전11시→하루시작 전날18:00 · 오후8시(리셋시각 지남)→당일18:00 · 경계마다 키 변경.
  테스트 1. 281 passed.

## 미출시(dev) · 2026-07-21 — 페이싱 '오늘 사용량' 실제값 표시 (사용자 지적 버그, Claude 구현)

- '오늘 0%p'가 실제로 많이 썼는데도 0으로 나오던 버그. **오진 정정**: 정수 양자화가 아니라(소스 7d%는
  이미 소수 25.12%였음) **7d 롤링% 델타가 롤오프에 상쇄**돼 오늘 사용을 못 잡은 것. 실측: 오늘 실제 6.37%인데 표시 0.
- 수정: `today_used_pct`(claude, 자정 이후 실제 소비 토큰 ÷ 7d상한) 추가. daily_pace_status에 today_used
  파라미터 — 있으면 그 값을 오늘 소비로 쓴다(7d% 델타 폴백은 유지). guiserver·cli·usage 콜러 배선.
  부수: `precise_weekly_pct`(정수 소스에 5%p 근접 시 토큰 정밀% 채택, 미보정 인플레 가드)로 누적도 정밀화.
- 실측 확인: claude 오늘 6.5%p로 표시(전 0%p). 상한은 전략에 따라(fixed=14 · catch_up=누적27%라 낮음).
  → fixed 선택 시 '오늘 6.5% / 상한 14%'로 사용자 기대와 일치. 테스트 1. 280 passed.
- 한계: codex는 transcript 토큰이 없어 today_used 불가(7d% 델타 유지). claude만 정밀.

## 미출시(dev) · 2026-07-21 — 페이싱 초과 처리 전략 옵션화: spread 추가 (사용자 요청, Claude 구현)

- 사용자: 초과분 처리 방식(즉시 조임 vs 균등 분산)을 **골라 쓸 수 있게 옵션으로**. 기존 fixed/catch_up에
  세 번째 `spread` 추가. `_spread_cap`·`_daily_cap`(전략 디스패처)로 분리.
  - **fixed**: 고정 q(14%). **catch_up**: 기준선 따라잡기(k×q−누적) — 초과 시 상한 즉시 확 낮추고 안 쓰면 회복.
  - **spread**(신규): 남은 예산(100−누적)÷남은 일수 균등 분배 — 초과분도 남은 날에 고르게 펴서 상한이 덜 급격히 하락.
  - 실측 차이(과사용 76%·리셋 3d23h): catch_up 상한 0%p(즉시 0) vs spread 6.0%p(균등). under(24%): catch_up 4 vs spread 12.7.
- `_pace_cfg` 화이트리스트에 spread 추가(모르는 값은 fixed 폴백). GUI 토글 3번째 버튼 `분산(남은일수 균등)` +
  상한 병기 조건에 spread 포함. 실브라우저 렌더 확인. 테스트 1(3전략 구분+폴백). 279 passed.
- ※ 별개로 남은 실제 버그: '오늘 사용량' 정수 7d% 측정이라 소액이 0으로 뭉갬 → 정밀 토큰 기반 측정으로 교체 필요(다음).

## 미출시(dev) · 2026-07-21 — 페이싱 '이후 지속가능 일일률' 표시 (사용자 요청, Claude 구현)

- 과사용해서 오늘 상한이 줄거나 0이 되면(catch_up이 이미 상한을 깎음) "오늘은 못 쓴다"만 보이고
  "그럼 다음날부터 하루 얼마씩 쓰면 되나"가 없었다. → `daily_pace_status`에 `forward_daily` 추가:
  **남은 주간예산(100−현재 7d%) ÷ 남은 일수** = 리셋까지 균등하게 쓸 수 있는 하루치.
- GUI 7d 바에 `· 이후 하루 ~X%` 표시(guiserver pace 객체 노출). 리셋 정보 없으면 생략(None).
- 실측 확인: codex 76%·리셋 3d23h → 오늘 상한 0%p인데 **이후 하루 ~6.2%**로 재개 가능량 안내.
  테스트: 76%→6.0 · 24%→12.7 · 리셋없음→None. 278 passed.

## 미출시(dev) · 2026-07-21 — [T1] 자동 트리아지 순수 규칙 (Claude 구현)

- `yok3x/triage.py::estimate_execution(spec)` — 착수 전 실행 형태를 **추천만**(자동 적용 X, 호출0, 의존성0).
  4축 분리(복잡도·실패영향도·검증가능성·비용) → {pattern, tier(direct|local|api), max_rounds(상한1~2),
  skip_review, confidence, axes, reasons}. 런 시작 시 `[triage]` 로그 + status.triage 기록(override 데이터 수집).
- 핵심 규칙(리포트 합의): **규모≠위험** — '한 줄 배포'는 저복잡이어도 고영향→api·검토생략 금지.
  **검토 생략은 가장 위험** → 저영향 AND 검증가능(verify_cmd) AND 저복잡만 후보. verify 없으면 절대 불가.
  복잡도·영향도 엇갈리면 신뢰도↓·사람판단 권장. escalate(실제 신호)는 유지, 트리아지는 앞의 시작점만.
- 한계 명시: 복잡도를 태스크 길이로 근사 → '장황하지만 쉬운 것 과대' 가능(리포트 인지). 추천 전용이라 무해.
  테스트 3(규모≠위험·검토생략 조건·반환 형태). 277 passed. 잔여: GUI 추천 배지(소).

## 미출시(dev) · 2026-07-21 — [E] few-shot 예시 필드 + [C정리] (Claude 구현)

- **E**: task spec `examples`(문자열/리스트) → build/revise(Resolver/생산자) 프롬프트에 `[예시]` 블록 주입.
  **ACQUIRE Questioner/Answerer(task_kind=general)·critic엔 주입 안 함**(조기가설 방지). 문자 상한
  `examples_max_chars`(기본 4000, clip). 예시 안 명령은 "지시 아니라 형식 참고"로 감싸 인젝션 완화.
  GUI textarea(c-examples) + buildSpec/openTask/resetTaskForm 배선. 테스트 6·실브라우저 검증. 274 passed.
- **C 블록 정리**: C-1~C-5로 이미 완료된 세부 항목들의 미체크 박스를 정리(원자적쓰기·ThreadPoolExecutor·
  취소계약·all-settled·예약누수·TOCTOU·lockfile·이식순서 — 전부 C-1~C-5 커밋에 구현·테스트됨).

---

*이전 이력은 [history/HISTORY-v001.md](history/HISTORY-v001.md) · [history/HISTORY-v002.md](history/HISTORY-v002.md)로 아카이브됨 (RULE §7.3 롤링). 인덱스: [history/README.md](history/README.md).*
