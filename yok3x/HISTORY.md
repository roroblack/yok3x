# HISTORY.md — 변경 이력

형식: `버전 · 날짜 시간 — 변경 요약`. 갱신할 때마다 맨 위에 새 항목을 추가한다.

---

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
