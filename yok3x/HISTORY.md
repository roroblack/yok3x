# HISTORY.md — 변경 이력

형식: `버전 · 날짜 시간 — 변경 요약`. 갱신할 때마다 맨 위에 새 항목을 추가한다.

---

## v3.3.0 · 2026-07-11 — 상황별 모델 프로파일(S1) + 프로듀서 병목 근본 해결

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
