# HISTORY.md — 변경 이력

- v4.x · 2026-08-30 — V-9 파일럿(실측 라운드 캘리브레이션 힌트): `calibration.py`가 이미
  기대하고 있었지만 한 번도 채워진 적 없던 `bucket` 필드를 매 런마다 자동으로 채우고
  (비용 0), bucket+pattern별 실측 rounds 중앙값·성공률을 `rounds_by_bucket`/`rounds_hint_for`로
  집계해 로그로만 노출하는 옵션(`automation.show_rounds_calibration_hint`, 기본 off)을
  추가했다. `max_rounds`는 자동으로 안 바꿈(관측만). 원래 제안(에이전트 종류·수 자동 결정)은
  실 데이터에 `producer-reviewer` 외 패턴이 전혀 없어 아직 불가능함을 확인. 상세:
  `docs/plans/v4.x-plan-rounds-calibration-hint-2026-08-30.md`.

- v4.x · 2026-08-30 — V-1 파일럿(재사용 가능한 "런북", StateM 이식): 검증 통과 라운드의
  접근을 고정 태그 어휘로 요약해 비슷한 신규 작업의 1라운드 프롬프트에 참고 힌트로 주입하는
  기능을 opt-in(`automation.use_runbooks`, 기본 False)으로 추가했다. T-1 mutation-testing
  파일럿으로 착수 조건이 재검토됐지만 표본이 작아(4/4 성공, 95% 신뢰 하한 약 50%) 전면
  구현이 아닌 제한적 파일럿으로 범위를 좁혔다(codex 설계 토론). 상세:
  `docs/plans/v4.x-plan-reusable-runbooks-2026-08-24.md`.

- v4.x · 2026-08-30 — BUG-54: `yok3x run/loop`이 task.json의 workdir와 CLI 실행 위치(CWD)가
  다르면 stderr에 경고를 출력하도록 추가했다(동작은 안 바꿈 — Config.load(".")가 CWD의
  yok3x.json을 조용히 적용해 backend가 뒤바뀌거나 비용 상한이 무효화되는 문제, T-2 #8에서
  $0.5648 낭비 후 발견). 상세: `docs/reports/bugs/BUG-54-cli-cwd-config-silently-overrides-task-workdir.md`.

- v4.x · 2026-08-30 — 구조화 리뷰 결함 목록(T-6)에서 SCORE를 결정론적 공식(severity별 고정
  감점+cap)으로 계산해 리뷰어의 자유형 SCORE를 대체하는 옵션을 추가했다
  (`cfg.yok3x["review_protocol"]["deterministic_scoring"]`, 기본 off). T-1 실측(동일 결함
  목록에도 SCORE가 stdev 1.6까지 흔들림)에 대한 직접 대응. 상세:
  `docs/plans/v4.x-plan-deterministic-review-scoring-2026-08-30.md`.

- v4.x · 2026-08-29 — CLI 시작 시 알려진 materialize 게시 부모에서 24시간 이상 된 고아 `.materialize-staging-*` 디렉터리만 정리하도록 추가했다.

- v4.x · 2026-08-28 — candidate backend별 실제 daily pace를 S4 관측 snapshot에 연결하고 역할별 현재값/보수적 후보를 기록하되 full 실행값은 바꾸지 않도록 고정했다.

- v4.x · 2026-08-25 — 직접 materialize를 stage-then-publish로 전환하고 커밋 매니페스트·고아 게시 경고를 추가했다.

- 2026-08-21: GUI 서버의 정상 반환·KeyboardInterrupt·메인 루프 예외·포착 가능한 SIGTERM 종료 사유와 shutdown 전후 로그/flush를 추가했다. 상세: `docs/reports/v4.9.0-gui-server-shutdown-observability-2026-08-21.md`.

## v4.8.0 · 2026-08-19 · S5 구조화 리뷰 프로토콜 관측·점진적 기본값 전환 준비 완료

- `review_protocol_observations.jsonl` append-only 관측 로그와 최근 표본·backend별 요약을 추가하고, producer-reviewer 라운드마다 source/parse error를 원문 없이 기록한다.
- S1~S5 구현 완료. 구조화 서명 기본 전환 및 score 게이트 독립 입력 승격은 T-6 데이터 검토 이후 별도 결정으로 남긴다.

## v4.9.0 · 2026-08-19 — 자동화 모드 전체 구현 완료

- S1~S9 자동화 모드 구현과 mock backend 기반 통합·운영 안전성 검증을 완료했다.

## v4.9.0 · 2026-08-18 — GUI 서버 응답 지연 조사

- `_routing_preview()`의 백엔드 availability 조회를 state build 내부에서 메모이즈하고, GUI Handler 소켓에 15초 유휴 연결 timeout을 추가했다. 조사 결과와 pytest 환경 제약은 `docs/reports/v4.9.0-assessment-gui-server-slowness-2026-08-18.md`에 기록했다.

형식: `버전 · 날짜 시간 — 변경 요약`. 갱신할 때마다 맨 위에 새 항목을 추가한다.

- 2026-08-20: GUI 설정 저장 직후 state snapshot을 동기 갱신해 정책·자동화·pace 토글이 이전 값으로 잠시 되돌아가던 BUG-52 수정.

- v4.9.0 쨌 2026-08-20: GUI `/api/state` 요청/probe 로깅을 추가하고, GUI state·backend 갱신을 백그라운드 snapshot/stale 캐시로 분리해 Codex app-server 지연이 HTTP 요청을 막지 않게 했다. 상세: `docs/reports/v4.9.0-gui-server-down-root-cause-2026-08-19.md`.

---

- 2026-08-20: GUI preconnect 연결 read 대기가 `/api/state` 지연으로 기록되던 BUG-51 수정. 상세: `docs/reports/v4.9.0-gui-server-alternating-slow-request-2026-08-20.md`.

## v4.6.0 · 2026-08-11 — S8 CLI 노출 및 S1~S9 전체 완료

- `yok3x sync <run_id>`가 run의 `understanding_bundle.json`을 claims 타입별, standard quiz, deep forensic 섹션으로 표시한다.
- `--drift`로 현재 코드와 이해 자료의 STALE 여부를 확인할 수 있으며, 번들이 없는 런은 안내 후 정상 종료한다.
- S1~S9 Cognitive Sync Layer 계획을 모두 완료했다. §9의 후속 후보는 후속 검토 항목으로 남긴다.

---

## v4.6.0 · 2026-08-11 — S7 Cognitive Sync Layer calibration 연동

- `yok3x sync-calibration`이 comprehension 적용/미적용 런의 결함감지율을 calibration.jsonl과 대조한다.
- 각 그룹 5개 이상에서 무상관이면 로그를 남기고 sync_layer를 자동 off하며, 설정 플래그로 비활성화할 수 있다.

---

## v4.6.0 · 2026-08-11 — S6c light/deep Cognitive Sync Layer

- `light`는 신규 LLM 호출 없이 기존 reviewer/critic 응답에 diff·로그·ACQUIRE 근거 연결 지시를 추가한다.
- `deep`는 T3(api)에서만 `deep_call_budget`을 지키며 forensic 질문을 생성하고 `deep_forensic`으로 저장한다. 실패·예산 초과는 런을 중단하지 않는다.

---

## v4.6.0 · 2026-08-11 — S6b standard 자동 comprehension 퀴즈 생성

- `standard` 모드에서 T2/T3 런에 한해 근거 있는 FACT/RECORDED_DECISION claim으로 질문을 최대 1회 생성하고 understanding bundle에 저장.
- 동일 diff/근거는 기존 bundle cache key와 worker replay cache를 재사용하며, 질문 생성 실패는 런을 깨뜨리지 않는다. 답변 평가 로직은 후속 단계로 남겼다.

---

## 미출시(dev) · 2026-08-10 — BUG-45: 균등/유동/분산·경고만/정지+승인·plan 세그먼트 버튼 저장 안 되는 버그(codex 교차검증)

- **발단**: 사용자가 "분산 눌러도 저장 및 적용 안 됨" 신고. 1차 조사에서 `_apply_config`(서버,
    `yok3x/guiserver.py`)의 `daily_pace.strategy` 화이트리스트가 `("fixed","catch_up")`뿐이라
    `"spread"`가 조용히 무시되던 것을 발견·수정(v3.6.0에서 spread 도입 시 갱신 누락).
  - 이 시점에 "고쳤다"고 보고했으나 사용자가 "고치긴 뭘 고쳐 아직도 안 되는데"라고 정정 — raw
      `fetch()` 직접 호출로만 검증하고 실제 버튼 클릭 경로는 검증하지 않은 게 원인이었다.
- **전수 점검 지시**("설정에 있는 다른 옵션들도 바로 적용 안되는 거 있나 체크해봐")에 따라 실제
    DOM `.click()` 디스패치로 전 세그먼트 재현 → **훨씬 큰 별개의 근본 원인** 발견:
    `gui/index.html`에서 `.seg` 클래스를 가진 모든 버튼의 `.onclick`을 페이지 로드 시 무조건
    덮어쓰는 범용 코드(`bb34742` v3.0.1 리브랜드 때부터 존재, 원래 `#c-pattern` 전용 의도)가
    이후 추가된 `#pacemodeseg`/`#pacestratseg`/`#planseg`의 `onclick="savePace(...)"` 등
    속성 핸들러를 통째로 가려 세 세그먼트 전부 "클릭하면 시각적으로만 눌리고 서버 저장은 전혀
    안 되는" 상태였다.
- **수정**: 범용 `.seg` 덮어쓰기 코드를 제거하고 `#c-pattern`(유일한 순수 시각적 세그먼트)
    전용으로 명시. **`codex exec`로 진단·수정을 교차검증**(사용자 지시: "코덱스랑 교차 검증
    하라고 내가 했잖아?") — codex가 진단은 정확하다고 확인하고, 1차 수정안
    (`hasAttribute("onclick")` 조건부 스킵)보다 대상을 명시하는 편이 장기적으로 더 안전하다고
    제안해 반영.
- **검증**: 4개 세그먼트(`#pacestratseg`/`#pacemodeseg`/`#planseg`/`#c-pattern`) 전부 실제
    `.click()` 디스패치로 재확인(저장 필요한 3곳은 서버 값 변경까지, `#c-pattern`은 회귀 없음
    확인). 신규 Python 테스트 2개(`daily_pace.strategy` 화이트리스트 회귀 방지). 전체 스위트
    Python 426 passed(+1 skip) / JS 9 passed. 자세한 내용은 `docs/reports/bugs/BUG-45-...md`.
- **교훈**: raw fetch 검증은 서버 로직만 증명할 뿐 클라이언트 클릭 배선은 증명하지 못한다 —
    "고쳤다"고 말하려면 실제 클릭 경로 끝까지 재현해야 한다. 하나의 증상 신고 뒤에 독립된 원인이
    여러 개 겹칠 수 있다는 것도 재확인.

---

## 미출시(dev) · 2026-08-10 — v4.6.0 S9: claim 클릭형 GUI(codex 디자인 컨설트) + BUG-44 발견·수정

- **S9 GUI 구현**(사용자 명시 승인 — "디자인 부분은 코덱스에게 시키고 니가 수정해"): codex에게
    실제 저장소를 읽게 하고 삽입 지점·스니펫을 상담받은 뒤 Claude가 구현. codex가 짚어낸 선행 조건
    (`/api/state`가 애초에 `understanding_bundle.json`을 안 내려주고 있었음)부터 처리:
  - `guiserver._recent_runs()`가 각 run에 `understanding:{claims:[...]}` 추가(sync_layer 미사용
    런은 `None` — GUI가 조용히 숨김).
  - `gui/index.html`: `.claims`/`.claim` CSS(기존 배지·색상 관례 재사용, FACT=파랑·DECISION=보라·
    INFERENCE=노랑·OPEN_QUESTION=빨강) + `renderClaims`/`toggleClaim`/`claimAction` — claim 클릭 →
    설명/퀴즈 버튼 노출 → 클릭 시에만 `POST /api/sync/claim_action` 호출(자동 아님) → 결과 인라인.
  - 라이브 브라우저로 전체 흐름 실제 검증(클릭→호출→결과 표시).
- **BUG-44(검증 중 우연히 발견)**: 이 작업과 무관한 기존(2026-07-24) 버그 — `paceTip` 계산이
    `t.pace===null`을 안 가려 `render()` 전체가 죽었고, `load()`의 catch가 이를 "서버 연결 끊김"으로
    오분류(실제로는 fetch 성공, 렌더 예외였음). `paceTipText()` 순수 함수로 추출해 가드 + 재발방지
    테스트. 자세한 내용은 `docs/reports/bugs/BUG-44-...md`.
  - **곁가지 확인**: 진단 중 잠깐 "한글이 깨졌다"고 오판할 뻔함 — 실제로는 이 세션 터미널의 cp949
    콘솔 출력 문제였을 뿐 파일·HTTP 응답은 처음부터 정상 UTF-8이었다(파일로 저장해 바이트 확인).
- 상태 지속성 버그도 하나 더 잡음(같은 검증 과정에서): `toggleClaim`이 DOM class만 토글해 7초
    폴링 재렌더마다 열림 상태가 사라짐 — `window._claimOpen`에 영속시켜 수정.
- 신규 테스트: Python 1(`_recent_runs`의 `understanding` 노출) + JS 9(`paceTipText` 2·`renderClaims`
    3 포함, 기존 4 유지) = 424 passed(Python) + JS 9 전부 통과.
- v4.6.0 계획서 S9 완료 표시.

---

## 미출시(dev) · 2026-08-08 — v4.6.0 S6a: 온디맨드 클릭형 claim 퀴즈/설명 API(사용자 제안)

- 사용자 제안: "코드 보다가 체크하고 싶은 부분이 있으면 에이전트 챗 로그에서 클릭해서 바로 퀴즈를
    받거나 설명을 볼 수 있게" — S6의 `standard`(위험도 T2+ 자동 트리거) 모드보다 **먼저 착수**. 자동
    트리거보다 싸다(LLM 호출이 사용자가 실제로 원할 때만 발생).
- `sync_layer.explain_claim_prompt`/`quiz_claim_prompt`: claim **하나**로 범위를 고정한 프롬프트
    (§3.4 "전체 저장소 대신 관련 부분만" 원칙). 근거 없는 확신을 요구하지 않고 "근거로는 알 수 없음"을
    명시하게 지시(§3.5 근거 있는 환각 방지). quiz는 암기형 질문 대신 흐름추적·반사실·불변조건·근거
    유형만 요구(원안 §7 문제 유형 채택).
- `guiserver.py`의 `POST /api/sync/claim_action`(`_sync_claim_action`): run_id 경로탈출 방어는
    review.py의 기존 `_safe_run_id` 재사용(중복 구현 안 함) · understanding_bundle 조회 · claim_id
    검증 · **요금 가드 정상 적용**(온디맨드라고 우회 없음, stop이면 호출 자체가 안 나감) · 기존
    `usage.record`로 원장 기록.
- 실측: 실제 GUI 서버(mock 백엔드)를 기동해 curl로 explain/quiz 둘 다 정상 응답 확인.
- 신규 테스트 7(프롬프트 범위 고정·경로탈출 거부·잘못된 action·번들 없음·claim_id 없음·정상 경로·
    가드 stop 시 호출 자체 안 나감). 423 passed.
- **GUI(실제 클릭 UI)는 미포함** — 이 커밋은 API까지만, `gui/index.html`은 건드리지 않음(별도 승인 필요).

---

## 미출시(dev) · 2026-08-08 — v4.6.0 Cognitive Sync Layer S1~S5(기계 조립, LLM 호출 0, 기본 off)

- 계획서 `docs/plans/v4.6.0-plan-cognitive-sync-layer-2026-08-08.md`(codex 공동설계)의 MVP 절반
    (S1~S5, 신규 LLM 호출이 전혀 없는 부분) 구현. 나머지(S6 light/standard/deep 모드·S7 calibration
    연동·S8 CLI·S9 GUI)는 후속.
- **신규 `yok3x/sync_layer.py`**(순수 함수, 의존성0): `build_understanding_bundle(run_dir, review_root,
    workdir)`가 F1-d 검토번들(`changes.diff`/`changes.json`)·`run.log`의 `[route]/[degrade]/[failover]/
    [gate]` 결정·ACQUIRE(`acquire.json`)를 **기계적으로 조립**해 `Claim`(FACT/RECORDED_DECISION/
    INFERENCE/OPEN_QUESTION) 목록을 낸다. 근거(evidence_refs) 없이 FACT/RECORDED_DECISION으로 분류될
    뻔한 claim은 자동으로 `OPEN_QUESTION`으로 강등(정직 표기 — 억지로 채우지 않음, codex의 "근거 있는
    환각" 경고 반영). ACQUIRE verdict 매핑은 acquire.py의 의미를 그대로 존중: confirmed→FACT,
    partial(위치 힌트로만)/contradicted(폐기)→OPEN_QUESTION.
- **Drift Detector**(`check_drift`): claim이 참조하는 파일들의 **콘텐츠 해시**를 조립 시점에 찍어두고,
    나중에 재조회 시 바뀌었거나(또는 파일이 사라졌으면) 그 파일을 근거로 쓰는 claim만 STALE로 표시.
    **정직한 한계**: 파일 단위 해시다(hunk·심볼 단위가 이상적이지만 AST 파서가 필요해 의존성0 범위
    밖 — 문서에 명시). 과소 무효화보다 과다 무효화가 안전하다는 원칙으로 보수적으로 설계.
- **정적 이해 체크리스트**(`static_checklist`): 기존 T1(triage) tier(direct/local/api)에 맞는
    LLM 없는 템플릿 질문. 모르는 tier는 가장 엄격한 `api`로 fail-closed.
- **`orchestrator._finish`**: `sync_layer.enabled`(**기본 False** — 다른 opt-in 기능과 같은 원칙, 새
    파일을 조용히 만들지 않음) 켜지면 run_dir·review_root 양쪽에 `understanding_bundle.json`/`.md`를
    쓰고 status.json에 claim 개수·tier 기록. 실패해도(폴백 가드) 런 완료 자체는 절대 안 깨짐(mat/changes
    와 같은 원칙 — BUG-39류 재발 방지).
- **설계 변경(계획서 대비 정직한 정정 2건)**: (1) 계획서는 `StepLog`에 changed_files/changed_symbols
    필드를 추가하자고 했으나, 구현 중 확인해보니 diff는 StepLog가 아니라 F1-d 검토번들에만 있어 그
    스키마 변경이 불필요했다 — `changes.diff`를 조립 시점에 직접 파싱하는 쪽이 더 단순하고 침습이
    적어 그렇게 함(StepLog 불변). (2) 계획서의 `mode`(off/light/standard/deep) 하나로만 설계된 설정을
    `enabled`(전체 기능 on/off) + `mode`(LLM 비용 단계)로 분리 — 이 프로젝트의 다른 모든 opt-in
    기능(daily_pace·worktree_isolation·auto_commit·mcp_servers 등)이 전부 이 2단 구조라 일관성 유지.
- 실측(mock 백엔드 실제 런): `[sync] 변경 이해 요약 2개 claim 조립(mode=off)` 로그 확인,
    `understanding_bundle.md`에 RECORDED_DECISION(run.log의 gate 결정) + tier 기반 체크리스트 정상 출력.
- 신규 테스트 15(파싱·verdict 매핑·조립 통합·강등 로직·캐시키·drift·checklist·렌더 12 + orchestrator
    통합 3). 416 passed.

---

## 미출시(dev) · 2026-08-08 — v4.1.0 MCP 워커도구 a1(설정 전달) — opt-in·화이트리스트·승인필수·fail-closed

- 계획서 `docs/plans/v4.1.0-plan-mcp-worker-tools-2026-07-15.md`의 a1(yok3x가 MCP 서버 설정을
    워커 CLI에 전달만 하고 실행은 워커가 함) 구현. codex 안전 리뷰의 강한 기본값을 사용자 확인 후
    그대로 적용: **opt-in·읽기전용 유지·화이트리스트·승인필수·감사로그·fail-closed**.
- **신규 `yok3x/mcp_policy.py`**(순수 함수, 의존성0): `resolve_mcp_grant(전역 화이트리스트, 워커설정)` —
    워커가 `mcp_tools`를 요청 안 했거나, 전역 `mcp_servers`(기본 빈 dict)가 비어있거나, 요청 서버가
    화이트리스트에 없거나, 유효한 `allow_tools`(`mcp__<server>__<tool>` 형식, **요청 서버 경계 안의
    것만**)가 하나도 안 남으면 **전부 빈 grant**(도구 없음)로 fail-closed. `write_mcp_config_file`은
    claude `--mcp-config`용 임시 JSON을 만들고 호출자가 정리, `record_grant`는 승인/거부 여부를
    `.yok3x/mcp_audit.jsonl`에 append(호출 실패해도 런을 안 죽임).
- **`_run_cli`(backends.py)**: `mcp_config_path`/`mcp_allowed_tools`가 주어지고 backend spec에
    `mcp_arg` 템플릿이 있을 때만 argv에 주입. **템플릿 없는 backend(codex/gemini)는 조용히 무시**
    (fail-closed) — claude만 `DEFAULT_BACKENDS`에 `mcp_arg`(`--mcp-config {path} --allowedTools
    {tools}`) 추가. 주입 시 기존 전면 `--disallowedTools`는 제거(도구 화이트리스트가 대신 통제).
- **`orchestrator.execute_call`**: 기존 승인 게이트 통과 뒤 `mcp_policy.resolve_mcp_grant` 판정.
    grant가 활성이면 **`_gate_mcp`(신규, `auto_approve`로 우회 불가) 승인을 별도로 또 받아야** 실행됨 —
    런 전체가 auto-approve여도 도구 사용 호출만은 매번 사람이 본다(계획서 codex 리뷰 "승인 필수"의
    강한 해석). 임시 mcp config 파일은 호출 직후(성공·실패 무관) 정리(비밀값 잔류 방지).
- **정직한 한계(코드·문서 양쪽에 명시)**: a1은 yok3x가 개별 도구 *호출*을 가로채지 않는다(워커 CLI
    런타임이 직접 실행) — 그래서 인자·경로·호스트 단위 실시간 검증은 이 계층에서 **불가능**하다.
    감사 로그도 "무엇이 *허가*됐는지"이지 "실제로 어떤 도구가 몇 번 *호출*됐는지"가 아니다. 호출 단위
    통제가 필요하면 계획서가 이미 후속으로 미뤄둔 **a2(yok3x가 직접 MCP 클라이언트)**가 선행돼야
    한다 — a2는 별도 계획으로 TODO 등록(지금 범위 밖).
- 신규 테스트 14(정책 판정 6·config파일/감사로그 2·argv 주입 2·오케스트레이터 통합 4). 401 passed.
- 실 MCP 서버 설치·검증은 사용자 몫으로 계획서에 이미 명시(P4) — 이번 구현은 그 전달 경로의
    안전장치(정책·게이트·감사·fail-closed)까지가 범위.

---

## 미출시(dev) · 2026-08-08 — R-5(Tier2·강등) Claude Code 로컬 JSONL 세션·모델별 토큰 귀속

- v4.4.0 계획서 R-5(리포트 7 흡수분) 구현. `limits.claude_usage_breakdown(conf, since, until)`:
    로컬 Claude Code JSONL(`~/.claude/projects`)에서 세션·모델별 토큰·호출수를 집계해 사후감사용으로
    낸다. **페이싱 앵커는 그대로 공식 reading**(v4.4.0 정정1 유지) — 이 함수는 별도 감사 뷰일 뿐 어떤
    가드·페이싱 계산에도 값을 공급하지 않는다.
- CLI `yok3x claude-usage [--days N] [--json]` 신규(스키마 `yok3x.claude_usage/1`).
- **리팩터(중복스캔 방지)**: 기존 `_file_usage_events`(페이싱 핫패스)와 신규 세션·모델 파싱이 같은
    JSONL을 두 번 읽지 않도록, 파싱을 `_file_usage_events_detailed`(ts·tok·session_id·model 4-tuple)로
    통합하고 `_file_usage_events`는 그 위의 경량 뷰(2-tuple)로 재정의. 캐시 1개 공유 — 이번 세션에서
    `probe()`·`codex_percent_at`·`list_models()`에 겪은 무캐시 중복스캔 패턴을 여기선 처음부터 피함.
- **폴백 가드**(Claude Code JSONL은 비문서·불안정 포맷): 줄 단위 파싱 실패·필드 누락은 그 줄만 건너뛰고
    계속(`session_id`/`model` 없으면 `"(알수없음)"`으로 묶임), `projects_dir` 없음도 빈 리스트로 정상 반환
    (예외 없음). 회귀 테스트로 `_file_usage_events`가 리팩터 전후 동일 값을 내는지 확인.
- 실측(실제 데이터): `yok3x claude-usage --days 3` — 세션 20여 개·모델 5종(opus-5/opus-4-8/sonnet-5/
    sonnet-4-6 등)별 토큰·호출수 정상 출력.
- 신규 테스트 7. 386 passed.

---

## 미출시(dev) · 2026-08-06 — BUG-43 여섯 번째 발견: list_models() 캐시도 스탬피드 (라이브 재현, 실제 30초 다운)

- 다섯 번째 발견 수정 후 약 하루 지나 재걸어둔 모니터가 **진짜 HTTP 다운**(연속 3회 실패, 30여 초
    뒤 자연 복구)을 실측으로 잡음. `py-spy` 덤프: 이번엔 codex가 아니라 `_gemini_bundle_models`
    (gemini 모델 목록, 키 없는 계정이 매번 떨어지는 경로)에 여러 스레드가 동시에 멈춰 있었다.
- **원인**: `list_models()`의 `_MODELS_CACHE`(TTL 5분)도 `probe()`·`codex_percent_at()`과 똑같이
    락이 없었다. `_gemini_bundle_models()`는 gemini-cli 번들의 `.js` 파일을 전부 읽어 정규식
    스캔하는 무거운 I/O — 5분 TTL이 드물게 열릴 때 동시 요청이 몰리면 다들 반복 스캔해 실제로
    서버 전체가 30초간 응답 불능이 됐다. codex 경로와 무관한 별개 함수에서 재발한 같은 문제 클래스.
- **수정**: `probe()`/`codex_percent_at()`과 동일한 백엔드별 락+더블체크락을 `list_models()`에
    추가(claude/codex/gemini/local 공통 적용). `_OAUTH_LIVE_CACHE`는 이미 `probe()`의 외곽 락
    안에서만 호출돼 별도 조치 불필요함을 확인.
- 검증: 신규 테스트(스레드 5개 동시 호출 → 실제 조회 1번만). **379 passed.** 배포 후 서버 정상
    응답 확인. BUG-43 리포트에 여섯 번째 발견으로 통합.
- 교훈: "비슷한 함수는 전수 점검할 가치가 있다"는 다섯 번째 발견의 경고가 하루 만에 실측으로
    맞아떨어졌다 — 같은 모양(TTL 캐시, 락 없음, 무거운 I/O)의 코드는 발견되는 대로 계속 고칠 것.

---

## 미출시(dev) · 2026-08-05 — BUG-43 다섯 번째 발견: codex_percent_at 무캐시로 인한 체감 지연 (라이브 스트레스 재현)

- probe() 캐시 스탬피드(네 번째 발견) 수정 후에도 같은 요청 패턴을 반복 재현하며 확인하던 중
    동시 요청이 여전히 12~16초씩 걸림을 실측(`py-spy`로 서버가 멈춘 게 아니라 여러 스레드가 각자
    일하는 중임을 확인 — 데드락 아님).
- **원인**: F2-10에서 추가한 `codex_percent_at`(오늘 소비 계산, `usage.py`의 `_pace_inputs`가 부름)에는
    `probe()`와 달리 캐시가 전혀 없었다. `build_state()`의 여러 호출 지점 + 동시 폴링 요청이 겹치면
    다들 독립적으로 세션 로그 전체(`read_text`/`stat`)를 다시 스캔 — probe()와 똑같은 모양의 스탬피드가
    이 함수에도 그대로 있었다.
- **수정**: `probe()`와 같은 락+TTL(15초) 캐시 추가(`_PCT_CACHE`, 키는 `(sessions_dir, at_ts,
    window_start, max_files)`). `at_ts`가 항상 과거 시각(`day_start`)이라 캐시 기간 내 인자 불변 +
    과거 시점 질의라 정확도 손실 없음.
- 검증: 신규 테스트(스레드 5개 동시 호출 → 실제 스캔 1번만). 라이브 재현: 웜업 16.5s→1.6s, 동시2건
    13~15s→3.4s, 순차3건 6.5~13s→0.4~0.6s. **378 passed.** BUG-43 리포트에 다섯 번째 발견으로 통합.
- 교훈: 캐시 스탬피드 방어는 "이 함수 하나"가 아니라 **비슷한 무거운 I/O를 하는 모든 함수**에
    같은 취약점이 없는지 점검해야 한다.

---

## 미출시(dev) · 2026-08-05 — BUG-43 네 번째 발견: 래퍼가 job 편입보다 먼저 죽는 경우 (라이브 재현)

- job-sweeper(세 번째 발견 수정) 배포 후 같은 요청 패턴(웜업+동시2+순차3)을 반복하며 확인하던 중,
    요청이 전부 끝난 뒤에도 `node.exe`+`codex.exe` 쌍이 계속 살아있음을 발견. `Win32_Process`로
    그 `node.exe`의 부모 PID를 조회하니 **이미 이 세상에 없는 PID**였다.
- **원인**: `codex.cmd`의 cmd.exe 래퍼가 진짜 작업(node.exe)을 띄운 직후 자기 자신이 먼저 종료해버릴
    수 있다. 기존 코드는 래퍼 PID의 job 편입이 실패하면 **job 전체를 버리고 스위퍼도 안 띄웠다** —
    래퍼가 이렇게 일찍 죽으면 그 편입은 항상 실패하고, 보호 장치 전체가 통째로 사라져 진짜
    node.exe/codex.exe는 무방비로 고아가 됐다.
- **수정**: 래퍼 자체의 job 편입 성공 여부와 무관하게 **스위퍼는 항상 띄운다.** 스위퍼는 `root_pid`를
    파이썬 쪽 추적 시작점으로만 쓸 뿐, 그 PID 자체가 job에 들어있을 필요가 없다 — 래퍼가 죽었어도
    그 자손은 스냅샷에 `ppid==root_pid`로 여전히 잡혀 스위퍼가 직접 편입할 수 있다.
- 검증: 신규 테스트(편입 실패를 모킹해도 job 유지+스위퍼 시작 확인, 기존 "편입 실패 시 job 포기"
    테스트를 대체). 라이브 재현: 같은 요청 패턴 반복 시 이전엔 남던 고아가 이번엔 0개.
- 교훈: npm `.cmd` 셈은 **래퍼 PID가 진짜 작업 프로세스보다 먼저 죽을 수 있다** — PID를 추적
    시작점으로만 쓰고 그 PID 자체의 생존에 의존하지 않는 방어가 더 견고하다.

---

## 미출시(dev) · 2026-08-05 — BUG-43 관련 세 번째 발견: probe() 캐시 스탬피드 (라이브 모니터링 중 실측, 192초 다운)

- Job Object+스윕 배포 후에도 라이브 모니터가 **192초(24회×8초) 다운 + 고아 4→7개 급증**을 실측.
    스택엔 `_kill_tree` 흔적 없음, 대신 **`node.exe`가 동시에 2개** 생존 확인.
- **원인(pre-existing, 이번 세션이 만든 버그 아님)**: `limits.probe()`의 캐시(`_CACHE`, TTL 15초)에
    **락이 없었다.** 동시 요청이 각자 캐시 미스를 보고 각자 codex app-server를 새로 스폰(캐시
    스탬피드). `build_state()` 하나가 내부에서 `check_backend('codex')`를 세 지점(메인 게이지·
    routing preview·coach)에서 부르는데, 첫 스폰이 캐시를 채우기 전이면 그 호출들도 각자 또 스폰.
    오늘 추가한 job-sweeper(스폰마다 0.15초 간격 프로세스 스냅샷 스레드)가 이 중복 스폰 하나하나를
    더 무겁게 만들어 — **본래 있던 버그가 오늘 수정으로 인해 더 아프게 드러난 경우.**
- **수정**: 백엔드별 `threading.Lock`으로 `probe()`의 캐시 미스 경로를 직렬화(더블체크락). 동시
    호출은 하나만 실제로 스폰하고 나머지는 락 대기 후 방금 채워진 캐시를 공유한다. CLI의 명시적
    `use_cache=False`(강제 새로고침)는 스탬피드 대상이 아니라 그대로 둔다.
- 검증: 신규 테스트 — 스레드 5개가 동시에 `probe()`를 불러도 실제 프로브는 **딱 1번**만 실행되고
    나머지 4개는 그 결과를 공유하는지 확인. **377 passed**. BUG-43 리포트에 세 번째 발견으로 통합.
- 교훈: 새 방어 계층(job-sweeper)이 **기존의 조용한 비효율(중복 스폰)을 무해→유해로 승격**시킬 수
    있다. 무거운 자원을 다루는 코드를 고칠 땐, 그 자원을 부르는 **호출 빈도** 자체도 함께 점검할 것.

---

## 미출시(dev) · 2026-08-05 — BUG-43 후속의 후속: job 편입 경쟁 발견·지속 스윕으로 해결 (라이브 모니터링 중 실측)

- Job Object 배포 직후 라이브 모니터가 **새 고아**를 실측으로 잡아냄(3개→4개). 재조사: job 편입은
    `Popen()` 직후 **한 번만** 실행되는데, 그 편입이 끝나기 전에 직계 자식이 이미 손자를 만들어버리면
    그 손자는 job 소속을 상속 못 받는다 — 시스템 부하가 있을 때 이 경쟁이 실제로 드러났다.
- **막힌 길**: `CREATE_SUSPENDED`로 만들어 편입 후 재개하면 경쟁이 원천 차단되지만, CPython
    `subprocess.Popen`이 메인 스레드 핸들을 생성 직후 즉시 닫아버려 표준 API로는 나중에 재개할
    방법이 없음을 확인 후 포기.
- **해법**: `_appserver_rate_limits`가 도는 동안 **지속적으로 스윕**하는 데몬 스레드 추가.
    `CreateToolhelp32Snapshot`(순수 ctypes, 서브프로세스 없이 빠름)으로 0.15초마다 {pid:ppid}
    스냅샷을 떠서, 이미 job에 속한 PID의 새 자손을 즉시 마저 편입한다(여러 단계를 한 틱에 다 따라잡음).
- 검증: ① **결정론적 재현 테스트** — 손자가 job 편입보다 먼저 뜨도록 일부러 기다린 뒤 "이미 진
    경쟁"을 강제하고도 스위퍼가 잡아내 같이 죽는지 확인(모킹 아님, 실제 3단 프로세스 트리).
    ② 실제 codex 바이너리로 **6회 연속** 프로브 → 새 고아 0개(시작 전/후 프로세스 목록 완전 동일).
- 신규 테스트 2(총 8개, BUG-43 관련). **376 passed**. BUG-43 리포트에 후속의 후속 섹션으로 통합.
- 교훈: "한 번 편입하면 끝"이라는 가정이 틀렸다 — 프로세스 생성은 비동기, 자손 생성 속도는 부하에
    좌우된다. 단발 조치보다 **관찰 기간 내내 지속되는 방어**가 타이밍 경쟁을 실질적으로 닫는다.

---

## 미출시(dev) · 2026-08-05 — BUG-43 후속: 고아 프로세스 진짜 근본원인 발견·Job Object로 해결 (사용자 재지적)

- 1차 수정(`capture_output`→`DEVNULL`) 직후 사용자: "이거 맞아? 근본적인 해결이 아닌 거 같은데?" —
    정확한 지적. 1차 수정은 **서버가 안 멈추게** 했을 뿐, **고아 codex 프로세스가 생기는 것 자체**는
    그대로였다. 라이브 프로세스 트리를 다시 실측했다.
- **진짜 근본원인**: `codex`가 `shutil.which()`로 `codex.cmd`(npm 셈)에 풀린다. yok3x가 `Popen`으로
    잡는 `proc.pid`는 **cmd.exe 래퍼**일 뿐이고, 실제 `codex.exe`/`codex-code-mode-host.exe`는
    `node.exe`를 거쳐 뜬다. `Win32_Process` 스냅샷으로 직접 확인: `_kill_tree` 실행 시점엔 **이미
    cmd.exe와 codex.exe의 부모-자식 연결이 끊겨 있다.** `taskkill /T`가 실패하는 게 아니라, 볼 수
    있는 트리 안에 대상이 아예 없는 것 — 타이밍에 따라 되기도/안 되기도 해 간헐적으로 관측됐다
    (고아 4개 누적 실측).
- **수정**: Windows **Job Object**로 컨테인. `codex.cmd` 프로세스를 띄운 직후 job에 편입해두면,
    이후 몇 단계를 거쳐 태어나는 자손도 **커널이 생성 시점에 job 소속을 자동 상속**한다(PID 추적
    불필요). `TerminateJobObject` 한 번으로 트리 전체가 죽는다. `_kill_tree`가 job을 **먼저** 종료하고,
    기존 taskkill은 job이 없거나 실패했을 때 폴백 + 이중 안전망으로 유지. 실패해도 예외를 삼키고
    조용히 taskkill-only 경로로 내려간다(비Windows는 즉시 no-op). ctypes만 사용(의존성0 유지).
- 검증(둘 다 실측, 모킹 아님): ① 독립 스크립트로 실제 codex.cmd 프로세스를 job 편입 후 종료 →
    새로 뜬 codex.exe만 정확히 사라지고 기존 고아는 안 건드려짐(통제 확인). ② 신규 테스트
    `test_job_object_really_kills_a_real_process_tree` — 실제 `cmd.exe→python.exe` 트리를 만들어
    job으로 둘 다 죽는지 스위트 안에서 직접 확인(Windows 전용, `tasklist`로 사후 검증).
- 신규 테스트 6(비Windows no-op·우선순위·폴백·편입실패 정리·실제 트리 킬 포함). **375 passed**.
    BUG-43 리포트에 후속 섹션으로 통합.

---

## 미출시(dev) · 2026-08-05 — GUI 서버 무한 대기 실측 진단·수정 (BUG-43, 사용자 지적)

- 사용자: "켜놔도 자꾸 꺼진다 — 다른 곳에서 끄는 건가? 모니터링하다 터지면 알려줘." 라이브 모니터
    (HTTP+프로세스+부모 3중 폴링)를 걸고 실측한 결과, **외부가 끄는 게 아니라 서버 스스로 데드락**.
- 진단: 프로세스·리슨 소켓은 살아있는데 모든 HTTP 요청이 타임아웃. `py-spy dump`로 실제 스레드
    스택을 떠 요청 처리 스레드가 `_kill_tree`(codex app-server 정리) 안 `subprocess.communicate()`의
    `join()`에서 무한 대기 중임을 확인. `tasklist`로 고아 `codex.exe` 3개·`codex-code-mode-host.exe`
    1개 누적도 함께 확인(같은 근본원인의 반복 실패 흔적).
- 근본원인: `_kill_tree`가 `subprocess.run(["taskkill",...], capture_output=True, timeout=5)`를 쓰는데,
    codex app-server가 남긴 손자 프로세스가 파이프 쓰기 핸들을 물고 있으면 출력을 모으는 리더 스레드가
    EOF를 못 받아 멈춘다. `timeout=5`는 프로세스 종료에만 적용되고, 예외를 던지기 전 그 리더 스레드를
    **타임아웃 없이 join**하는 CPython 내부 경로 때문에 사실상 무한 대기가 된다(Windows subprocess 함정).
    `build_state()`가 매 요청마다 codex 상태를 거치므로 새 요청도 전부 같은 지점에서 멈춘다.
- 즉시 복구: 고아 프로세스 4개를 강제 종료해 막혀있던 요청이 즉시 완료됨을 실측으로 확인(인과관계 검증).
- 수정: `capture_output=True` → `stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`(taskkill 출력은
    필요 없음 — 파이프 자체를 만들지 않아 이 경로를 원천 차단). 신규 회귀 테스트 1. 370 passed.
- 남은 위험(정직 표기): `taskkill /T`가 손자 프로세스를 왜 못 잡았는지는 별개 문제(codex CLI의 detached
    스폰 가능성) — 이번 수정은 그 상황에서도 서버가 멈추지 않게 할 뿐, 고아 자체를 막지는 못한다.

---

## 미출시(dev) · 2026-07-27 — 첫 로딩 지연 조사: **캐시를 늘리지 않고** 일을 줄여 해결 (사용자 지적)

- 사용자: "처음 로딩이 왜 이렇게 오래 걸리나?" → 실측 `build_state` **콜드 8.9s / 웜 1.3s**.
    분해: claude 트랜스크립트 **326파일 345MB 최초 파싱 ~3s**(프로세스당 1회) + **오늘 추가한 codex 시계열
    스캔이 매 호출 0.22~0.27s**(캐시 없음 — F2-10에서 내가 BUG-38의 교훈을 새 코드에 적용하지 않았다).
- 처음엔 "캐시 추가 + 디스크 영속화"를 제안했으나, 사용자가 **"그건 검증된 해법이자 검증된 버그 아니냐"**
    고 지적. 맞다 — 이 프로젝트에서 캐시·파생상태는 반복적으로 버그의 원천이었다(BUG-30·33·34·36·37·38).
    345MB 파생본을 디스크에 영속화하는 건 BUG-32/36/37 계열을 부르는 설계다. **제안을 철회하고 방향을 바꿨다.**
- **채택한 해법(캐시 아님 — 불필요한 일을 안 한다)**:
    ① **정의상 0 단축**: `at_ts <= window_start`면 그 시점 누적은 **정의상 0%** → 파일을 한 개도 읽지 않는다.
       리셋 당일에는 `day_start == window_start`라 이 경로가 늘 타는데, 없으면 '관측 없음'을 확인하려고
       창 안 파일을 전부 훑었다. **220~270ms → 0.0ms.**
    ② **증명 가능한 조기 종료**: 파일 안 이벤트 시각은 그 파일 mtime보다 늦을 수 없다 → mtime이 이미 찾은
       최선값보다 이르면 그 파일도, 더 오래된 나머지도 최선을 못 바꾼다 → 읽지 않고 중단. **~250ms → 54ms.**
    둘 다 **정확도 손실 0**이고 새 무효화 지점을 만들지 않는다. `build_state` 웜 1.32s → **1.15s**.
- claude 콜드 ~3s는 345MB를 한 번 읽는 고유 비용이라 **그대로 둔다** — 없애려면 디스크 파생본이 필요하고,
    그건 위 이유로 위험 대비 이득이 낮다(정직 표기).
- 검증: 경계 테스트 2 추가(창 시작 시점·그 이전 모두 0.0, 파일 미존재에도 동작). **369 passed**.

## 미출시(dev) · 2026-07-27 — F2-7 구현 완료: 재개를 prefix→집합으로(병렬 fanout 재개 해제)

- 계약서 §3 수용 기준을 **테스트로 먼저 쓰고**(4건 전부 실패 확인) 구현했다 — 계약→테스트→구현 순서 준수.
- **구현**: `_load_replay_prefix` → **`_load_replay_steps`**(집합 기반). 손상·스키마미달·실패·번호중복은
    **그 항목만** 제외하고 나머지 성공 step은 살린다(C-2). 이전엔 하나만 깨져도 이후 전부를 버려서,
    병렬에서 무관한 워커의 성공 결과까지 날아갔다. 제외 사유를 요약해 반환(조용한 폐기 금지).
- **게이팅 해제**(C-1/C-4): fanout·fanout-fanin·**parallel 켜짐**·**materialize/changes** 모두 재개 허용.
    materialize/changes는 출력 루트가 `yok3x-out/<run_id>`로 런마다 격리돼 이전 산출물을 덮어쓰지 않는다.
    **acquire만 제외 유지**(C-5) — preflight LLM 호출은 step 재생 대상이 아니라 재개 시 쿼터를 다시 쓴다.
- **안전 근거**: `call_key`가 단계 번호를 빼고 프롬프트 전체를 담으므로 순서 독립이며, 상류가 바뀌면
    하류 키가 달라져 자동 재실행된다 → 집합 재생에서도 잘못된 재생이 구조적으로 불가능(실증 확인).
- 기존 재개 테스트 1건은 **구 정책**(fanout·parallel·materialize 전부 거부)을 검증하고 있어 새 계약으로
    갱신했다 — **회귀가 아니라 정책 변경**임을 테스트 docstring에 명시.
- 한계 정직 표기(C-6): 재현되는 것은 **호출 결과**뿐 — 실행 순서·동시성은 재현되지 않는다.
- 검증: 신규 테스트 4 + 재개 스위트 17 passed, 전체 스위트 통과(exit 0).
- **관측(비용)**: 오늘 추가한 git 기반 테스트(worktree·ratchet)가 실제 git을 띄워 전체 스위트가
    ~30초 → **~4분**으로 늘었다. 기능상 문제는 아니지만 반복 실행 부담이라 마커로 분리할 여지가 있다(후속).

## 미출시(dev) · 2026-07-27 — F2-7 계약 선확정: 병렬 재개의 진짜 걸림돌은 하나뿐이었다

- 계획서가 요구한 "비결정 순서·부분완료 manifest 계약 선확정"을 구현 전에 문서로 못박았다
    ([계약서](reports/v4.5.0-f2-7-parallel-resume-contract-2026-07-27-1830.md)).
- **핵심 발견(실증)**: `call_key`는 `worker|task_kind|backend|model|prompt|read_only` 해시이고 **단계 번호를
    의도적으로 제외**한다 → **순서 독립 재생이 이미 성립**한다. 게다가 프롬프트 전체가 키에 들어가므로
    상류 산출물이 바뀌면 하류 키가 자동으로 달라져 **재실행된다**(잘못된 재생이 구조적으로 불가능).
    3성질(동일호출 일치·상류변경 시 불일치·워커별 분리)을 직접 확인했다.
- 따라서 오래 미뤄둔 "비결정 순서" 문제는 **이미 해결돼 있었고**, 실제 걸림돌은
    `_load_replay_prefix`가 **번호 연속 prefix만** 인정하는 것 하나뿐이다(병렬은 완료가 집합이라
    2번이 성공해도 1번이 실패하면 버려진다).
- 계약: 재생=**집합**(C-1) · 손상은 **항목 단위 제외**(C-2) · 부분완료는 **step 파일이 단일 진실**(별도
    manifest 신설 안 함, C-3) · materialize/changes는 run_id 격리라 **허용**(C-4) · acquire는 **제외 유지**
    (쿼터 재소모, C-5) · **실행 순서는 재현 안 됨을 정직 표기**(C-6, "완전한 결정적 재현" 주장 금지).
- 수용 기준 5개 확정. **잔여: 구현** — 수용 기준을 테스트로 먼저 쓰고 착수한다.

## 미출시(dev) · 2026-07-27 — F2-8: JS 테스트 러너 결정(node:test) + GUI 순수 함수 테스트 착지

- **러너 결정: Node 내장 `node:test`**(v26 확인). jest/vitest/mocha는 전부 npm 의존성이라 **의존성0 원칙**상
    배제. Node가 없는 환경에서는 skip — Python 테스트는 Node 없이도 전부 돌아야 하므로 하드 요구로 만들지 않았다.
- **GUI를 한 글자도 수정하지 않고** 테스트한다(RULE §5.6). `gui/index.html`은 인라인 `<script>` 한 덩어리라
    보통은 파일을 쪼개야 하지만, 대신 **원본을 읽어 함수 소스만 중괄호 균형으로 추출**해 `node:vm`
    샌드박스에서 평가하는 하네스를 만들었다.
- 대상: `esc`(**BUG-19 회귀 방지선** — 산출물 HTML이 이스케이프 없이 innerHTML에 들어가 DOM이 붕괴했던 건),
    `fclsPct`·`wlvl`(창별 임계 분류, BUG-20 계열), `fmtTok`·`fmtDur`(0과 '측정 불가(—)' 구분).
- **하네스 유효성 실증**: `esc()`를 일부러 훼손(`<`만 처리) → **그 테스트만 실패**, 원복 → 4/4 통과.
    "통과만 하고 아무것도 못 잡는 테스트"가 아님을 확인했다.
- pytest 래퍼로 **단일 진입점 유지**(`pytest`가 JS까지 함께 실행, node 없으면 skip). **365 passed**.

## 미출시(dev) · 2026-07-27 — F2-10 해결: codex '오늘 소비'를 rate_limits 시계열로 복원

- 세션 초반 사용자 지적("codex는 리셋 시점 기준 갱신을 전혀 못 한다")의 **근본 해결**. 그동안 codex는
    토큰 트랜스크립트가 없어 '오늘'을 현재값 스냅샷으로만 쟀고, 프로세스 재기동에 취약했다(BUG-37 계열).
- **발견**: codex 세션 로그(`rollout-*.jsonl`)의 `token_count` 이벤트에 **timestamp + 주간 used_percent**가
    함께 남는다 → **주간 %의 시계열이 이미 디스크에 있다**(사용자 질문에 답하려 로그를 뒤지다 확인).
- **구현**: `limits.codex_percent_at(conf, at_ts, window_start)` — 하루 시작 시점의 %를 되찾아
    `현재% − 하루시작%`로 오늘 소비를 낸다. 디스크 기반이라 **재기동에 불변**.
- **핵심 함정(실측으로 잡음)**: 시계열은 **주간 리셋을 가로지른다**. 창 경계를 주지 않으면 리셋 직전의
    이전 창 누적(80%)을 새 창 기준선으로 잡아 **오늘 소비가 0으로 뭉개진다**. `window_start` 이전 관측을
    배제하고, 창 안에 관측이 없으면 0%(창이 막 시작 = 사용 없음)로 처리.
- 실측: `pace`가 codex **주간 27% · 오늘 27% / 상한 14% → warn**을 정확히 표시(이전엔 오늘 0%).
- 검증: 신규 테스트 2(시계열 판독·창 경계 3분기 / 페이싱 배선·폴백). **364 passed**.

## 미출시(dev) · 2026-07-27 — F2-5 완료: 비원자 writer 감사·전환(공용 헬퍼) + 기반 항목 해제

- 전 쓰기 지점을 훑어 **비원자 5곳을 원자적으로 전환**하고, 나머지는 **전환하지 않는 근거**를 남겼다.
- **전환**: ① `scaffold`의 `yok3x.json`·`backends.json` — **BUG-32(0바이트 손상으로 전체 기동 불가)와 같은
    파일인데 이 경로만 비원자로 남아 있었다**(save만 고쳐졌던 것). ② `knot.save`·`write_context`·`write_brief`
    — 고정 경로이고 **매 런 프롬프트로 주입**되므로 찢긴 쓰기가 곧 오염된 입력이 된다.
- **유지(근거 기록)**: 일회성 scaffold 플레이스홀더·런별 고유 spec 파일·`.bak` 백업·런 전용 final_output은
    단일 writer이거나 사본이라 torn write가 상태를 망가뜨리지 않는다 — **무분별한 전환은 과설계**라 남겼다.
- 공용 헬퍼 `config.atomic_write_text`로 통일(같은 패턴이 흩어져 있던 중복 제거). 실패 시 tmp를 정리한다.
- 실증: `replace` 중 예외를 주입해도 **원본 보존 + tmp 잔재 0**. 신규 테스트 2. **362 passed**.
- 이로써 기반(cross-cutting) 항목 "상태 파일 쓰기는 임시파일 후 atomic replace"도 **완료** 처리.

## 미출시(dev) · 2026-07-27 — F2-3: 계획된 수정을 실측으로 기각(basetemp 고정 안 함)

- 계획은 "pytest basetemp를 `.tmp/pytest`로 **고정**해 루트 오염 차단"이었다. 구현 후 **실측으로 두 가지를 확인**:
    ① `addopts`의 `--basetemp`는 **CLI 인자에 진다** — `--basetemp=...`를 주면 그대로 생성된다.
    즉 오염의 실제 원인(도구가 `--basetemp=.pytest-tmp...`를 직접 넘김)을 **막지 못한다**.
    ② tmp_path가 **저장소 안**으로 들어가 git 관련 테스트 의미가 바뀐다 — worktree 테스트 2건이
    '비-git 디렉터리'를 기대했는데 상위 yok3x 저장소를 찾아 실패했다(실제 회귀).
- **부작용은 있고 목적은 미달성 → 되돌렸다.** 계획서에 적힌 대로 구현하는 것보다, 구현 후 재보니 틀렸다는
    사실을 남기는 게 맞다고 판단(근거는 pyproject 주석과 체크리스트에 기록).
- 결론: pytest 기본값(시스템 temp)이 이미 저장소 밖이라 기본 경로로는 루트를 안 더럽힌다.
    실효 방어는 `.gitignore`의 `.pytest-tmp*/`·`.codex-pytest-tmp*/`(이미 존재).
- **미해결 1건(정직 표기)**: 루트의 `.pytest-tmp-f1b`(07-21 잔재)는 **열람·삭제 모두 권한 거부**(WinError 5)라
    제거하지 못했다. gitignore 대상이라 저장소엔 영향 없으나 원인 확인 필요(프로세스 잠금 추정).
- 360 passed(회귀 없음).

## 미출시(dev) · 2026-07-27 — F2-11 완료: 잔여 3축 감사(상한·base·TOCTOU) — 대부분 건전, 1건 보완

- 계획서가 지목한 잔여 3축을 **실증 점검**했다. 결론: **설계가 이미 대체로 건전**했고 실제 결함은 1건뿐.
- **거대단일파일 상한우회 = 없음**: 단일 600KB→거부, 400KB×10→**정확히 2MB에서 절단**(수락 5/거부 5),
    30개→20개 절단. 크기 계산이 `len(content.encode("utf-8"))`라 문자수 트릭(멀티바이트)도 통하지 않는다.
- **base 2MB = 이미 안전**: `st_size`를 믿지 않고 **열린 스트림에서 MAX+1 바이트를 읽어** 판정한다 —
    stat 후 파일이 커져도 무제한으로 읽지 않는 올바른 패턴.
- **TOCTOU(accept) = 이미 견고**: 교체 **직전** `recheck_base()`가 심볼릭·경로이탈·**base sha256**을 재확인하고
    원자적 replace. 중간에 원본이 바뀌면 그 파일만 중단된다.
- **보완 1건**: 후보(candidate) 읽기만 `st_size` 확인 후 **무제한 `read_bytes()`**였다(같은 파일의 다른 경로는
    경계 읽기를 쓰는데 여기만 예외). base와 동일한 경계 읽기로 통일 — 코드베이스 자체 패턴과의 일관성 회복.
- 검증: 상한 실증 3케이스 + 기존 review 테스트 30건 통과. **360 passed**. → **F2-11 완료**.

## 미출시(dev) · 2026-07-27 — F2-11 감사: 절대경로 verify_cmd 우회 실증·완화(BUG-42) + 런당 비용 상한

- **BUG-42(실증 재현)**: F1-f 스테이징은 `cwd`만 격리하는데 `verify_cmd`는 임의 셸 명령이라 **절대경로를 쓰면
    원본 트리를 검증**한다. 재현 결과 스테이징엔 CANDIDATE를 넣었는데 명령은 원본의 ORIGINAL을 읽고도
    라벨은 `candidate`였다 — **T-1 지상진실이 거짓이 되는 경로**(calibration이 이 라벨만 라벨로 씀).
- **완화**: stdlib으로 임의 셸 명령 샌드박싱은 불가 → **막지 못해도 거짓 라벨은 막는다.**
    `verify_cmd` **인자**에 절대경로가 있으면 `verify_scope="untrusted_verify_cmd"`로 강등 + 사유 로그.
    소비자는 `=="candidate"`만 인정하므로 오염 관측이 자동 배제된다.
    **정밀도**: 첫 토큰(실행 파일)은 제외 — venv 인터프리터 절대경로는 정상이며, 이 구분이 없으면 기존
    정상 픽스처가 오탐된다(첫 구현에서 실제 3건 오탐 → 수정). 탐지 11케이스 오탐·미탐 0.
- **남은 한계 정직 표기**: 환경변수·`cd ..`·심볼릭·상대 상위경로로는 여전히 우회 가능. 이건 보안 경계가
    아니라 **흔한 조용한 실수**를 잡는 완화다. 진짜 신뢰 경계는 OS 수준 격리가 필요(의존성0 범위 밖).
- **런당 실지출 상한**(`guard.reservation.max_usd_per_run`, 기본 0=off): preflight 추정이 실제를 100배
    과소평가한 T-2 실측($0.03 vs $3.37)에 대한 대응 — 추정이 아닌 **실측 누적**으로 다음 호출 전에 차단.
- 검증: 신규 테스트 5. **360 passed** · 버그리포트 42건 인덱스 일치.

## 미출시(dev) · 2026-07-27 — T-2 1차 수집 완료(12런) + 사전등록 기준 판정 · BUG-41

- **F2-4 사전등록**([프로토콜](reports/v4.5.0-t2-protocol-preregistration-2026-07-27-1150.md)) 후 12런 수집.
    [결과 보고서](reports/v4.5.0-t2-collection-round1-results-2026-07-27-1240.md). 판정은 **기준을 바꾸지 않고** 적용.
- **판정 ①(미충족)**: `verify_ok=False`가 **0건**(기준 ≥3) → **T-1 본분석 착수 불가**. 12개 작업이 전부
    2라운드 안에 통과 — 작업군이 프로듀서에게 너무 쉬웠다. 한쪽 라벨만으론 상관·혼동행렬이 퇴화한다.
- **판정 ②(재현됨)**: N0′ 우려 — **테스트를 통과했는데 SCORE<8.0이라 반려**된 관측이 **정확히 3건**
    (7.5·7.0·6.0, 전부 gate 반려). 혼동행렬 tp=11·fp=0·tn=0·**fn=3** → **작동하는 코드의 21%가 반려**.
    `03_rle`은 테스트 통과에도 점수 미달로 최종 반려(종료코드 3 — F2-2 계약대로).
- **판정 ③(불가)**: "게이트가 불량을 걸러내는가"는 실패 표본 0건이라 **측정 불가**. 임계 재설정은
    사전등록대로 **이번 표본으로 확정하지 않는다**(tn=0이라 임계를 낮출 때의 대가를 잴 수 없다).
- **BUG-41**: `calibration.summarize`가 `corr=None`(계산 불가)을 '상관 낮음'과 같은 분기로 처리해
    데이터 없이 "게이트 무의미 의심"을 표시했다 — 하필 이번 표본이 그 경우. `판정 불가(라벨 한쪽뿐)`로 분리.
- **비용 실측**: 총 **$8.10**(중단조건 $10에 근접). 10런은 $0.07~0.12인데 2런($3.37·$3.03)이 **79%**를 차지 —
    파일럿 기반 추정 $2~4가 빗나간 원인. **예산 예측은 평균이 아니라 꼬리로 잡아야 한다**(런당 상한 필요).
- 하네스 건전성: `original_tree` 12.5%(임계 30% 이하) · R-2 재시도는 전부 '새 증거' 근거로 승인됨.
- 검증: 신규 테스트 1. **347 passed** · 버그리포트 41건 인덱스 일치.

## 미출시(dev) · 2026-07-27 — T-2 파일럿 실측 + 조용한 열화 2건 수정 (BUG-40)

- **T-2 파일럿 2런 실행**(사용자 승인) — 목적은 비용 실측. 결과: **런당 $0.11~0.32 · 1~2분**,
    claude 주간 **3% → 3%**(변화 없음, 5h만 +1%p), codex 주간 12% → 13%. **T-2 전체(10~15런) 환산 $2~4 ·
    주간 1%p 안팎**. 이전 추정(0.2~1.2%p)보다도 저렴 — 추정 대신 측정한 것이 옳았다.
    신규 `run_id` 원장 필드로 **런 단위 비용 집계**가 처음 가능해졌다(비쿼터 선행작업).
- **BUG-40(파일럿이 잡아낸 실제 버그 2건, 둘 다 '조용한 열화')**:
    ① `workdir` 없이 `verify_cmd`만 설정하면 후보 스테이징이 불가해 **산출물이 없는 트리에서 검증** →
    통과하는 코드가 매 라운드 거짓 실패(실측: 테스트 5개 전부 통과하는 산출물이 2라운드 fail). 사유를
    명시 로그 + 런 시작 `[warn]`으로 노출(RULE §5.5).
    ② 새 프로젝트는 캡 미보정이라 트랜스크립트 추정이 **995%**(실측 3%)로 나오고 가드가 **모든 런을 stop**.
    oauth 경로에만 있던 비현실성 가드(ratio>2.0)를 statusline 폴백에도 적용 → 원장 폴백으로 넘어가 차단 해소.
- 부수 확인: BUG-35 클램프가 실환경에서 정확히 회당 +25%로 동작(28.0B→35.0B→43.8B→54.7B). 단 파생값이
    커서 **단조 상승**이 관측됨(스윙은 없음) — 캐시read 포함 회계 특성. 페이싱은 OAuth 실측 %를 쓰므로 표시 영향 없음.
- 검증: 신규 테스트 3. **346 passed** · 버그리포트 40건 인덱스 일치.

## v4.5.0 · 2026-07-26 — 릴리스: 병렬 안전성(worktree 격리 + auto-commit 래칫) · BUG-39

- 2026-08-20: GUI 작업 편집 폼에 `changes.apply_mode` 스위치(review 기본/auto_commit opt-in) 추가 및 서버 검증 보강.

- v4.4.0 zip 이후 들어온 **R-7 1·2단계**와 **BUG-39**를 묶어 릴리스. 기능 추가가 있어 minor 상향(4.4.0 → 4.5.0).
- 내용: 워커별 git worktree 격리(opt-in) · auto-commit 래칫(전용 브랜치 전용, 기본은 review=사람수락) ·
    로그 인코딩 크래시로 작업이 유실되던 BUG-39 수정.
- 패키징(RULE §8): 스모크(`init`/`setup`/`run`(mock, exit0)/`limits`/`--json` 2종) 통과 →
    `release/yok3x-v4.5.0.zip`(124 files·503KB), zip 내부 `__version__=4.5.0` 검증,
    `backup/versions/` 보존(해시 일치), 이전 v4.4.0 zip은 보존본 확인 후 `release/`에서 정리.
    RULE §9 트리거①(minor 상향) → 정본 폴더 스냅샷 `backup/yok3x-v4.5.0-20260726-1749/`. **344 passed**.

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
