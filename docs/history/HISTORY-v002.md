# HISTORY 아카이브 v002 — 2026-07-16 ~ 2026-07-21

`docs/HISTORY.md`에서 롤링(RULE §7.3)된 엔트리 39개. 활성 이력은 `docs/HISTORY.md`.
인덱스: [README.md](README.md).

---

## 미출시(dev) · 2026-07-21 — [G-2] producer-reviewer 재개 (**Claude 구현** — 역할 정정 후 첫 작업)

- 로드맵 G-2. 기존 재개(G-1)는 순차 pipeline만 허용했으나, replay는 `call_key`(프롬프트 내용 해시) 기반이라
  **패턴 무관**하다. 막던 건 `_resume_supported`의 pipeline 한정 게이트 하나 → producer-reviewer 허용으로 완화.
- **escalate/stall 상태는 별도 코드 없이 재계산됨**: 재생된 라운드 결과(결정적)로 루프를 재실행하면
  prev_sig·escalated·producer/reviewer 교체·artifact 누적이 원래와 동일하게 재현된다. call_key도 동일해져
  완료 라운드는 재생, 중단 지점부터 실제 실행.
- 제외 유지(pipeline과 동일): parallel(비결정 순서)·acquire(preflight LLM)·materialize/changes(루프 밖 부작용).
- 테스트 2개: ①라운드1(producer+reviewer) 재생→라운드2 실행→게이트 통과, ②**escalate로 producer가
  codex-main으로 교체된 뒤 중단→재개 시 라운드3 producer=codex-main 재계산 확인**(G-2 최대 위험 검증). 268 passed.
- ※ 역할 정정 후 첫 작업: **Claude 구현**. codex 검토는 codex 쿼터 75%라 보류(요청 시 가능).

## 미출시(dev) · 2026-07-21 — [F1-g] review bundle 수락/거절 CLI (codex 구현·Claude 검토)

- `yok3x review <run_id>`로 workdir/run_dir 기반 번들을 자동 탐색해 status·바이트·상한 적용 diff를 읽기전용 표시.
- `--accept [경로 ...]`는 경로/심볼릭·현재 base SHA-256·후보 proposed SHA-256을 파일별 순수 판정
  (`review.decide_file_application`)한 뒤 같은 디렉터리 임시 파일→`os.replace`로 승격한다. 신규 충돌과
  스테일 base는 중단하고 나머지는 계속(all-settled). 쓰기 직전 심볼릭/외부경로 재확인(TOCTOU 완화).
- `--reject`는 workdir를 바꾸지 않고 번들 `review.log`에 거절 결정을 남긴다. 강제·hunk·worktree·GUI·재실행 없음.
- **Claude 독립 검토(E2E 실측)**: ①base 일치 수락 → 적용, ②**스테일(번들 생성 후 원본 변경) → 중단·미적용,
  사람 수정 그대로 유지**(핵심 안전), ③거절 → 트리 불변·review.log 기록. **266 passed**(신규 8). LLM 호출 0.
- ※ 이 작업까지 "codex 구현·Claude 검토" 패턴. **이후 역할 정정**: 구현=Claude 메인 / 검토=codex(가벼운 리뷰만).

## 미출시(dev) · 2026-07-21 — [F1-f] 후보 스테이징 verify — **T-1 언블록** (codex 구현·Claude 검토)

- F1-b가 격리만 하던 것(verify가 후보 아닌 원본 트리 검사, BUG-25)을 **실제 수정**. producer-reviewer
  라운드의 `file:` 후보를 workdir 격리 사본에 전체 파일로 적용한 뒤 그 cwd에서 `verify_cmd`를 실행한다.
  후보 결과가 `evaluate_score_gate`의 verify 입력이 되며 calibration에 실제 대상 따라 `verify_scope=
  candidate|original_tree` 기록. → **이제부터 materialize/review + verify_cmd 런은 유효 라벨(candidate) 축적**.
- 원본은 복사원으로만 읽고 `.git`·의존성/런 산출물·캐시·심볼릭 링크를 제외. 후보 경로는 artifacts 검증 +
  resolve/심볼릭 방어. **하나라도 거부되면 fail-closed로 원본 폴백**(부분 적용=reviewer가 본 후보와 불일치 방지).
  시스템 temp는 성공·실패 모두 `finally`에서 정리. 상한(`changes.stage_max_files` 기본 5000) 초과·실패 폴백.
- 게이트 판정·**reviewer blind(작업E)**·materialize/review 동작 불변.
- **Claude 독립 검토(실제 pytest subprocess E2E)**: broken base + 수정 후보 → verify **통과**(scope=candidate),
  broken 후보 → **실패**, **원본 workdir 불변**(원본 foo.py BUG 그대로), 스테이징 잔여물 **0개**,
  후보 verify_ok가 게이트로 흐름, rev_blocks에 verify_out 없음(blind 유지). **258 passed**(신규 8).
- 리포트: `docs/reports/bugs/BUG-25-verify-ignored-worker-candidate.md`. **T-1 선행조건 해소**(TODO 반영 필요).

## 미출시(dev) · 2026-07-21 — [F1-d] 수정모드 review bundle MVP (codex 구현·Claude 검토)

- 사용자 제안 "수정모드". codex 합의: 새 패턴 아닌 공통 change-set 게이트. 이번은 **읽기전용 번들 생성까지**.
- 모든 패턴의 `_finish` 공통 후처리에 task `changes:{"mode":"review"}` opt-in 추가.
  materialize와 독립적으로 `file:` 출력 계약을 켜고 후보·`changes.diff`·`changes.json`을 런별 격리 루트에 보존한다.
- workdir 파일은 UTF-8 base로만 **읽어** new/modified/unchanged와 SHA-256·바이트를 기록한다.
  위험 상대경로·workdir 밖 해석·심볼릭·비 UTF-8/NUL 파일은 스킵·기록하며 **원본 적용/verify는 하지 않는다**.
  `_review_root`는 materialize의 사용자지정 root와 달리 **항상 고정 격리**(workdir/yok3x-out/<run_id>).
- **codex 자체 구현 중 잡은 것**: 원본 덮을 수 있던 custom root → 고정 격리 root, 마지막 개행 없는 diff 손상,
  CRLF 파싱 변조, 예약 메타파일(changes.json/diff) 충돌 → 회귀 테스트로 보강.
- **Claude 독립 검토(E2E 실측)**: workdir 원본 파일 **불변 확인**(수정 후보 게시 후 원본 sha256 그대로),
  후보가 격리 루트에만 쓰임, status(modified/new) 정확, base_sha256이 실제 원본과 일치, diff에 수정 반영.
  잔여(codex 신고): base 2MB 상한·비UTF-8 제외·로컬 공격자 디렉터리교체 TOCTOU(로컬 도구라 저위험). **251 passed**(신규 7).


## 미출시(dev) · 2026-07-21 — [F1-b] verify 원본검증 오염 방지 (codex 구현·Claude 검토)

- 확정된 문제: producer 프롬프트가 "파일 편집 말고 텍스트로만 답하라"(orchestrator.py) →
  워커는 후보를 workdir에 쓰지 않는다. 그런데 `_run_verify`는 workdir에서 실행 →
  **후보 미반영 원본 트리를 검증**. calibration의 verify_ok가 SCORE가 평가한 후보와 무관(라벨 오염).
- 최소·안전 조치(진짜 후보검증은 F1-d 스테이징에서): calibration FIELDS에 `verify_scope` 추가,
  현재 producer-reviewer 관측을 **`original_tree`**로 기록. `_labeled`가 **`candidate`만** 지상진실로
  인정(fail-closed) → 과거·현재 오염 관측을 상관·혼동에서 제외. `summarize`는 지금 데이터에 n_labeled=0.
- 게이트 동작(strict/advisory)·프롬프트·verify 실행위치 **불변**. 라벨 신뢰성만 격리.
- **Claude 검토가 잡은 것**(codex 자진신고): `scripts/todo_check.py`의 준비도 필터가 verify_scope를
  안 봐서 original_tree를 유효로 세 **준비도 과대계상** → candidate만 세도록 수정(실증: orig 12→0, cand 12→12).
- 결과: T-1(캘리브레이션 검증)은 사실상 **F1-d 스테이징 완료가 선행**임이 명확해짐(TODO/계획서 반영).
- 244 passed(신규 2 + todo_check 수정).

## 미출시(dev) · 2026-07-21 — [F1-a/c] advisory 게이트: SCORE 권한 분리 (codex 구현·Claude 검토)

- 배경: PDF 교육자료 원칙("실제 테스트 합격 코드를 AI 주관 점수만으로 떨어뜨리지 말자") + 웹리서치
  5단계 검증 사다리(객관 verify=L3 vs LLM SCORE=L4는 다른 층) + N0' 실측(정상코드 전량반려).
- `score_gate_mode`(task 전용, 기본 strict): strict=현행 `SCORE≥th AND verify`,
  advisory=`verify가 통과 결정`·낮은 SCORE는 반려 대신 `review_required` 플래그. **임계 8.0 불변**.
- 순수 판정 함수 `evaluate_score_gate`(외부상태 미접근)로 분리 → 진리표 테스트 용이.
  advisory+verify_cmd 없음 = **워커 호출 전 RunAborted(config_error)**, 조용한 strict 폴백 금지.
  런 디스패치·run_producer_reviewer 양쪽서 사전검증(저장된 task의 잘못된 mode도 enqueue 전 거부).
- **F1-c**: 최종 status에 `gate`{mode·passed·verify_ok·score·threshold·review_required·reason} 보존.
  `done`(실행완료) 의미는 유지하되 게이트 결과는 별도 필드로 → done=합격 오해 해소. prod 실패 시
  기본 gate(`not_evaluated`, passed=False) 보존. calibration에 `gate_mode` 필드 추가(advisory는 조기종료라 분포 상이).
- GUI: pass_score 옆 select(strict/advisory, **auto 없음**·보류). buildSpec/openTask/resetTaskForm 배선.
- 검토(Claude): 진리표 정확(strict 저점→반려 유지, advisory verify통과+저점→통과+플래그), advisory 통과 시
  루프 즉시 종료(라운드 낭비 없음), self.gate 초기화·엣지케이스 안전. 실브라우저 select 렌더 확인.
  **242 passed**(신규 20). scope guard 준수: 임계 불변·auto 보류·state failed 미변경·리뷰어 blind 유지.

## 미출시(dev) · 2026-07-20 — [작업E] 심판 캘리브레이션 **라벨 누출 제거** (codex 발견·구현·Claude 검토)

- **핵심 발견(codex)**: producer-reviewer 루프가 verify를 먼저 실행하고 그 통과/실패 결과를
  **리뷰어에게 보여준 뒤** SCORE를 받고 있었다. 즉 SCORE가 verify_ok의 예측이 아니라 **정답을 본 후 평가**.
  calibration.jsonl의 (SCORE, verify_ok) 상관이 오염 → 작업B의 "수집 파이프라인 완비"는 캘리브레이션
  목적상 틀렸다. (아이러니: N0'에선 심판을 빈 디렉터리에 격리해 pass/fail을 숨겼는데, 제품은 대놓고 노출 중이었음.)
- 수정: (E-1) rev_blocks에서 verify 결과 블록 제거 + 일반/적대적 리뷰 지시문의 verify 참조 제거 →
  리뷰어는 산출물·rubric만 보고 **독립 채점**. (E-2) 하드 게이트 `score>=th AND verify_ok` 불변 —
  SCORE 9라도 verify 실패면 불통과. (E-3) verify 실패 진단은 **다음 라운드 producer**에게 전달(수정 계속).
  (E-4) 호출 수 불변(라운드당 producer 1 + reviewer 1, 리뷰어 2배 호출 없음).
- 부수 이점: 기존엔 리뷰어가 "테스트 실패→저점"을 앵무새처럼 반복해 SCORE 독립정보 희박.
  blind면 SCORE가 테스트가 못 잡는 품질을 재고, verify 하드게이트와 합쳐 독립 신호 2개가 됨.
- 잔여 위험(codex 신고, 하네스 레벨 아님): producer가 진단을 산출물에 복사하거나 도구형 리뷰어가
  run.log를 뒤지면 간접 노출 가능. 호출 인자 기준 누출은 제거됨.
- Claude 검토: artifact가 다음 라운드 producer 전에 prod.text로 교체되므로 리뷰어는 append된
  verify_out을 못 봄(누출 없음 확인). clip은 앞70%/뒤30% 보존이라 끝에 붙은 verify 진단이 살아남음.
- 222 passed(신규: blind 검증 파라미터화 2 + 하드게이트/진단전달 1). scope guard 준수: 임계 8.0 불변.

## 미출시(dev) · 2026-07-20 — [작업B] 심판 캘리브레이션 실데이터 수집 강화 (codex 구현·Claude 검토)

- N0'가 드러낸 "게이트 임계 8.0이 정상 코드 전량 반려(최고 SCORE 7.0)"에 대응. 새 임계를 정하려면
  **실제 런의 (SCORE, verify_ok) 쌍**이 필요한데 `.yok3x/calibration.jsonl`이 비어 있었다. 수집 결함 3건 수정.
  **임계값 8.0 자체는 안 바꿈**(합성 데이터 N0'로 정하면 CLIP 교훈 위반 — 실 verify 신호로 정해야, codex 판단).
- B-1: `make_record(**kw)`가 모르는 키를 조용히 버려 `verify_passed=` 오타가 `verify_ok=None`이 되던 것
  → 미지 키 `TypeError`. (N0'에서 나를 속인 그 버그)
- B-2: 매 라운드 `_calib` 덮어써 최종 라운드만 기록하던 것 → `_calib_rounds` 리스트로 **라운드별 전부 기록**.
  초기=저점·후기=고점이라 임계 보정에 필요한 SCORE 전 구간이 남는다. run_id·round로 상관 클러스터링 대비.
- B-3: 스키마에 `reviewer`(실제 심판 backend, 폴오버 반영)·`threshold`·`gate_pass`·`round` 추가.
- 핵심 확인: verify는 이미 게이트 판정보다 **먼저 점수와 무관하게** 실행됨(orchestrator.py) →
  정답 코드가 8.0에서 반려되는 구간(FN)이 실제로 데이터에 남는다. 테스트로 검증(저점 3.0/5.0도 verify_ok 기록).
- **Claude 적대적 검토가 잡은 2건**(codex 원안의 데이터 함정): ① `rounds`가 `round`와 동일값이 되어
  총량 의미 상실 → rounds=총 라운드 수 복원. ② 런합계(tokens·cost·duration·issues)를 전 행에 반복 →
  파일 합산 시 과대계상 → 마지막 행에만 싣고 나머지 None. 둘 다 회귀 테스트 추가.
- 219 passed(신규 5). scope guard 준수: 임계 변경·적응형 게이트·재개·사후최적임계 계산 안 함.

## 미출시(dev) · 2026-07-20 — [N0] 심판 교정 backfill **불가 판정** + 변이 기반 대안(N0') 제안

- 계획서 v5.0.0의 최우선 항목 N0(과거 패치를 baseline/candidate 쌍으로 재생해 SCORE 교정) 실행.
- **라벨링 방식은 검증 성공**: BUG-21 수정커밋의 부모에 worktree를 만들고 현재 회귀테스트를 역적용 →
  `test_rename_task_same_name_updates_label_in_place` **정확히 실패**(=부모가 broken이라는 기계적 라벨 성립).
  LLM 없이 무료로 정답 라벨을 만들 수 있음이 확인됨.
- **그러나 표본이 없다**: 최근 40커밋 전수조사 결과 라벨 가능 쌍 **실질 1~2개**. 우리 버그 다수가
  **GUI/JS**(pytest 라벨 불가: BUG-19·20·22)거나 **기능 추가**(75e6c5e)거나 **라이브 환경 의존**(BUG-17·18).
  우리 자신의 기준(`calibration.summarize`의 n<10=표본부족)에 미달 → **n=2 LLM 채점은 통계적 무의미이므로
  실행하지 않음**(비용 회피). 자동 채택 금지(N3)는 그대로 유지.
- **대안 N0'(변이 기반 하한 검사)**: 정상 코드에 결함 주입 → **pytest 결과를 무료 라벨**로 → 심판 블라인드
  채점 → point_biserial/confusion. 실현성 확인(변이 3종 즉시 적용 가능, 대상 6,163줄·라벨러 216테스트).
  **한계 명시**: 변이 결함은 LLM 실제 결함과 분포가 달라 **하한 검사**로만 유효.
- 부수 발견: 수정과 회귀테스트를 같은 커밋에 넣는 습관 탓에 옛 시점에 실패 테스트가 없다 →
  **failing test first** 커밋 습관이 향후 라벨 데이터를 자연 축적시킴.
- 보고서: `docs/reports/v5.x-assessment-n0-judge-calibration-backfill-2026-07-20.md`

## 미출시(dev) · 2026-07-18 — [로드맵 3 / C-4] ACQUIRE Answerer 병렬 이식 (codex 구현·Claude 검토)

- `call_workers_parallel`(C-3)의 **첫 실사용자**. ACQUIRE는 질문마다 독립 읽기전용 Answerer라 병렬화의
  이상적 대상(codex 원 설계도 "Answerer 먼저"). 질문마다 `prepare_call(read_only=True)`로 CallSpec 만들어
  병렬 실행 → 결과를 질문과 zip해 순서 보존 처리(parse/validate/수집). 실패 슬롯(None)은 스킵(all-settled).
- 나머지 로직 100% 보존: Questioner 단일호출·게이팅·verify_evidence·apply_verdicts·render·_save_acquire
  (knot 미저장)·fail-safe. RunAborted 전파. parallel.enabled off면 순차 폴백(call_workers_parallel 위임).
- 검토(적대적): **병렬 0.46s vs 순차 1.40s(3배)** · 순서 보존(느린 답 먼저 와도 질문 순) · read_only=True 유지 ·
  all-settled(한 Answerer 실패해도 나머지 QA 수집) · 양 경로 결과 동일. **175 passed**(신규 6).

## 미출시(dev) · 2026-07-18 — 7일 바에 페이싱+리셋 시간 동시 표시 (사용자 요청)

- 페이싱이 켜지면 `오늘 N%p / 상한 M%p`가 `리셋 X 후`를 **덮어써서** 리셋 시간이 사라지던 것 수정.
  이제 `오늘 N%p / 상한 M%p · 리셋 X 후 · 사용/남은 토큰`을 모두 표시. 대시보드 7d 바(claude·codex).

## 미출시(dev) · 2026-07-18 — 워커 산출물을 실제 파일로 게시(materialize) (사용자 요청·codex 공동설계)

- **사용자 문제**: "_test_tmp에 계산기 만들라고 시켰는데 결과물이 없다." 원인: 워커는 텍스트 생산자라
  (claude --disallowedTools로 Write/Edit 차단) 코드가 답변 텍스트로만 나오고 파일이 안 생김.
  실제 계산기는 `.yok3x/runs/<id>/final_output.md`에 완성돼 있었다. = 멀티에이전트 리뷰 G2 격차.
- **설계(codex 반박 수용)**: 코드블록에서 파일명 추측(A안)은 데모 휴리스틱 → 폐기. 워커가 `​```file:<상대경로>`
  로 **경로를 명시**한 블록만 게시. `yok3x/artifacts.py`(순수): 파싱 + 경로검증(절대/드라이브/UNC/`..`/
  윈도우예약어/끝공백점/대소문자충돌/디렉터리만 거부) + 개수·크기 상한 + 덮어쓰기 기본 금지.
- orchestrator `_materialize_outputs`: 임시파일→`os.replace` 원자적 게시, **해석된 실제 경로가 루트 내부인지
  재확인(심볼릭 차단)**, `workdir/yok3x-out/<run_id>/`에 격리, sha256 감사. **텍스트 성공≠게시 성공**을
  status에 별도 기록. opt-in(`spec.materialize`), 기본 꺼짐이면 기존 동작 100% 동일.
- GUI: 고급 옵션에 '산출물을 파일로 게시' + '덮어쓰기 허용' 토글. codegen 프롬프트에 게시 시 file: 계약 주입.
- 검토(적대적): file:펜스만 추출(언어펜스 무시)·위험경로 9종 거부·경로탈출 차단(../hack.js가 workdir 밖에
  안 생김)·tmp 잔여물 없음·overwrite/대소문자·기본 꺼짐. **169 passed**(신규 5).
- 에이전트 선택: 대시보드 실측상 claude 여유(5h 94%/7d 92%) > codex(페이싱 13/14%p 소진) → claude(=Claude)가
  구현, codex는 설계 논의만. (claude live가 429여도 어제 만든 자동 캘리브레이션 덕에 실측% 정상 표시.)

## 미출시(dev) · 2026-07-17 — [로드맵 3 / C-3] 진짜 병렬 call_workers_parallel (codex 구현·Claude 검토)

- **여기서 처음 스레드 도입**(C-1 준비/실행 분리, C-2 예약·락 위에). `guard.parallel.enabled` **기본 false**
  — 꺼지면 순차 폴백으로 기존 동작 100% 동일. 호출부는 아직 안 바꿈(C-4/C-5에서 이식).
- codex가 앞서 경고한 함정 전부 처리: 제어 스레드서 예약+배치승인+step index 선할당(worker는 실행만) ·
  입력순서 반환 · all-settled(실패 슬롯만 None) · backend별 Semaphore · **취소 계약 실구현**(backends가
  Popen을 쓰고 orchestrator가 실행 중 프로세스를 추적해 종료 — Future.cancel()로는 못 죽임) ·
  finally에서 예약 해제.
- 검토(적대적 프로브 9종): 입력순서 · all-settled · **worker thread 게이트 호출 0회** · step index 무결성/정렬 ·
  예약 해제(성공·예외 모두) · 순차 폴백 · RunAborted 전파 시 완료분 보존 · backend별 상한 준수.
  **실측 병렬성**: 4건×0.35s → **0.50s**(순차 1.40s), 동시최대 4 / per_backend=2면 동시최대 2, 0.82s.
- ⚠ 리뷰 과정 기록: 첫 측정이 2.80s로 나와 병렬 결함을 의심했으나, `execute_call`의 `usage.check_backend`가
  **실제 네트워크 probe**를 도는 것이 원인이었다(내 프로브 오염). 스텁 후 재측정해 병렬 정상 확인.
- **164 passed**(신규 11).

## 미출시(dev) · 2026-07-17 — effort '기본'이 실제로 뭔지 표시 (사용자 요청)

- **문제**: 드롭다운의 `effort 기본`이 **무슨 값인지 안 알려줌**. 실제로는 yok3x가 `--effort`/`-c` 플래그를
  **아예 안 보내서** 각 CLI의 자체 기본이 적용되는데, 그 기본은 backend·사용자 설정마다 다르다.
- `backends.effort_defaults()`: 알아낼 수 있는 것만 **실제로 읽어서** 알려준다(추측 금지 §5.5).
  codex=`$CODEX_HOME|~/.codex/config.toml`의 `model_reasoning_effort` 정규식 조회(키 하나뿐이라 TOML
  파서 불필요 — `tomllib`는 3.11+라 3.10 호환 위해 한 줄만 파싱). claude=""(CLI/세션이 정하며 비공개).
- GUI: 옵션 라벨이 `effort 기본 (xhigh)`처럼 **실제값 표시**, 모르면 `(CLI 기본값)`. 툴팁에 출처·지원 레벨.
  yok3x 전역 `default_effort`가 있으면 그게 우선임도 반영.
- **확인된 사실**: 이 환경의 codex 기본은 **xhigh**(config.toml). claude는 도움말에 기본 미표기이나
  Claude Code 안내 문구상 **Opus엔 medium 권장**이며, `ultrathink`가 고효율을 유발하는 프롬프트 키워드다
  (= effort 값이 아님 — 앞선 ultracode 판단과 일치).
- 검증: codex config 실조회(xhigh)·CODEX_HOME 존중·파일 없어도 예외 없음·GUI 표시. 153 passed.

## 미출시(dev) · 2026-07-17 — 작업 이름을 CRUD 줄로 이동('라벨' 정체 해소) (사용자 요청)

- **문제**: `라벨` 필드가 실제로는 **작업 이름**(저장 파일명·작업별 보기 그룹)인데, placeholder가
  "작업별 콘솔 그룹(선택)"이라 **선택적 태그처럼** 보이고 폼 깊숙이 숨어 있었다. `saveTask()`는
  `name = 라벨값`으로 그대로 쓴다.
- **함정 발견**: 저장된 작업을 열어 라벨만 고치고 💾 하면 **이름 변경이 아니라 '다른 이름으로 복제'**
  → 작업이 두 개가 됨(원본 잔존). `✏️ 이름 수정` 추가 후엔 이름 변경 경로가 둘이라 더 위험해졌다.
- **수정**: 라벨 입력을 **CRUD 줄로 이동** + 이름을 `작업 이름`으로 정직하게 표기
  (`작업별 보기 [선택] · 작업 이름 [입력] · ➕📂▶💾✏️🗑`). 저장된 작업 선택 시 **읽기전용**으로 전환해
  복제 함정을 원천 차단(변경은 ✏️로 일원화). 폼의 라벨 필드는 제거.
- 검증: CRUD 줄 구성·폼에서 제거·저장작업 선택 시 읽기전용/툴팁 전환·전체 선택 시 편집가능 확인. 153 passed.
- 부기: 이전 브라우저 테스트 잔재(`task-이름바꿈.json`) 완전 정리 — 파일시스템·서버 양쪽 0개 **확인까지** 수행.

## 미출시(dev) · 2026-07-17 — [BUG-21] 작업 이름 수정 자기충돌 수정 (사용자 보고)

- `✏️ 이름 수정`에서 **이름을 안 바꾸고 확인만 눌러도** "같은 이름의 작업이 이미 있다"로 실패하던 버그.
  프롬프트가 현재 이름을 미리 채워주므로 가장 흔한 경로였다. 원인: `_rename_task`가 `new_path.exists()`만
  보고 **자기 자신(new_path == old_path)을 제외하지 않음**.
- 수정: `same_file`이면 충돌 아님 → 파일 이동 없이 label만 갱신·원본 삭제 생략. **다른 작업과의 진짜
  충돌은 그대로 거부**(원본·충돌대상 보존). 회귀 테스트 추가. **153 passed**. 리포트 BUG-21.
- 부기: 이 버그가 드러난 `이름바꿈` 작업은 **Claude의 브라우저 테스트 잔재**였다(삭제 호출만 하고 확인
  안 해 남음). 정리 완료 — 테스트 아티팩트는 삭제까지 검증할 것.

## 미출시(dev) · 2026-07-17 — '새 작업'을 진짜 작업 생성으로 + 이름수정 + workdir UI (사용자 요청)

- **핵심 지적**: `➕ 새 작업`이 폼만 비워서 **작업을 여러 개 만들어 오가며 이어서 작업할 수 없었다.**
  → 이제 **이름을 입력받아 draft 작업을 실제 생성** → `작업별 보기`에 `💾 <이름> (0)`으로 추가 → 자동 선택.
- **draft(빈 목표) 작업 허용**: `_validate_task_spec(allow_draft=True)`로 저장은 되게, **실행은 차단**
  (`_saved_task_for_run` → "목표가 비었다 — 작업을 열어 목표를 입력하라"). 빈 목표로 런 시작 방지.
- **이름 수정 `✏️`**: `_rename_task` + `POST /api/task/rename`. 새 파일 먼저 쓰고 성공 시 원본 삭제(유실 방지),
  **중복 이름 거부 시 원본 보존**. spec 내용(목표·pattern·agents·workdir) 그대로 보존.
- **workdir UI 일관화**: 작업별 workdir에 `✕`(지우기)·`📁`(찾기) 추가 — 전역 워크스페이스엔 있는데 없었음.
  전역과 달리 즉시 저장 안 하고 [💾] 저장 시 `spec.workdir`로 들어감(작업 폼의 일부라서).
  확인: workdir 설정 시 그 작업의 워커 cwd·verify_cmd 위치·레포 컨텍스트 기준이 실제로 바뀜.
- 검토: 적대적 프로브 통과 — draft 저장·실행차단 · rename 내용보존/원본삭제/중복거부/원본보존/없는작업 오류 ·
  GUI 왕복(➕→목록 등장→자동선택→✏️ 이름변경→파일명까지 갱신). **152 passed**(신규 6).

## 미출시(dev) · 2026-07-17 — 작업별 에이전트 배치(전역 기본 + override) + CRUD 아이콘·새작업 (사용자 요청)

- **작업별 에이전트 배치**: task spec에 `agents` 부분 override 추가. `Orchestrator._worker(name)`이
  `dict(cfg.worker(name))` 복사본에 `agents_override[name]`만 병합 — orchestrator의 `cfg.worker()` 직접호출을
  전부 교체(prepare/execute·`_ensure_cross_family`·acquire). **전역 config는 절대 안 바뀜**(런 스코프).
- GUI: 설정탭 = **에이전트 배치 보드(전역 기본)**, 콘솔 = **작업별 배치**(전역값으로 표시 →
  바꾸면 `● 작업별` 배지 + `↺` 되돌리기). `buildSpec()`이 **바뀐 워커만** `agents`에 담음(깨끗한 spec).
- **CRUD 아이콘화**(➕📂▶💾🗑, 라벨은 툴팁) + **누락됐던 `➕ 새 작업`** — [열기]로 폼이 채워지면
  새 작업을 시작할 방법이 없던 문제(사용자 지적). newTask()는 override도 전역으로 초기화.
- 검토: 적대적 프로브 통과 — override 없으면 전역과 동일(회귀) · 부분 override 시 나머지 전역 유지 ·
  **전역 불변** · 반환값 수정이 전역에 안 샘 · GUI 왕복(변경→배지→spec.agents→↺→복귀→newTask). 146 passed.

## 미출시(dev) · 2026-07-17 — [로드맵 3.5] claude 추정 자동 캘리브레이션 + 토큰 수 표시 (codex 구현·Claude 검토)

- **실통증 해결**: 실측(live)이 죽으면 사용량이 원장($0/$5)으로 떨어져 무의미해지던 문제. 원인은 트랜스크립트
  추정이 **cache read 토큰까지 합산**해 ~150배 과대(7d 768% vs 실측 5%) → 200% 가드가 버림(BUG-15 방어).
- `limits.autocalibrate_claude()`: 라이브 성공 시마다 `cap = rolling_tokens / (live%/100)` 역산해
  `limit_5h/7d_tokens` 저장(수동 `yok3x calibrate` 로직 자동화). CLIP 교훈("평가자를 지상진실로 보정")과 동일.
  가드: min_calib_pct(1%)·rate-limit(600s)·비현실 캡·미미변화 스킵. 네트워크 호출 추가 0(로컬 트랜스크립트만).
- `Window`에 `used_tokens`/`limit_tokens`(None=미측정) → GUI가 추정 경로에서 **사용/남은 토큰량** 표시
  (라이브는 %만 주므로 None 유지 — 사용자 요청이 '자동갱신 꺼진 경우'였음).
- **검토서 결함 2개 발견·수정**: ①내 스펙의 '100배 초과 무시' 가드가 **정상 보정(7d ~153배)을 거부** →
  `max_calib_multiple`(1000) 설정으로 분리(하드코딩이라 §5.5 위반이기도) + 회귀 테스트 2개 추가.
  ②저장 알림이 `logger.info`라 핸들러 미구성 시 **안 보임** → 코드베이스 관례대로 `print("[calib] ...")`.
- **실측 검증**: 보정 후 5h 16.0% vs 실측 16.0%, 7d 5.0% vs 실측 5.0% — **차이 0.0%p**. 138 passed.

## 미출시(dev) · 2026-07-17 — 토큰 갱신 시 refreshTokenExpiresAt 보존 + 추정 캘리브레이션 계획 (사용자 요청)

- **수정**: `_refresh_claude_token`이 서버가 `refresh_token_expires_in`을 주면 `refreshTokenExpiresAt`를
  저장하도록. 안 주면 **기존 값 유지**(추측 금지 §5.5). — 이건 **재로그인과 무관한 표시 정확도 버그**였다:
  갱신은 저장된 refreshToken만 있으면 되므로 기능은 정상, 다만 낡은 만료일이 '재로그인 필요' **오경보**를 냄.
- **진단(사용자 질문 "추정으로 수치 못 띄우나")**: 구조는 **이미 있음**(`claude_transcripts` → 5h/7d 롤링,
  폴백 체인 live→stale(15분)→추정→원장). 그러나 추정이 **캐시 read 토큰까지 합산**해 ~150배 과대
  (실측: 7d 5% / 추정: 38억tok=768%) → 200% 가드가 버리고 원장($0/$5)으로 떨어짐(BUG-15 방어로 의도된 동작).
- **해결 계획**(체크리스트 3.5 삽입): 실측 성공 시마다 `cap=rolling_tokens/(live%/100)` 역산해 자동 보정
  (수동 `yok3x calibrate` 로직 재사용) + Window에 사용/남은 토큰 수 노출. CLIP 교훈("평가자를 지상진실로
  보정")과 동일 원리.

## 미출시(dev) · 2026-07-17 — claude 토큰 자동갱신 토글 + 최근 런 목록 줄 제한 (사용자 요청)

- **토큰 자동갱신 GUI 토글**(설정·한도 탭, claude 요금제 아래): 다른 토글(가드·폴오버·오프라인·페이싱)과
  달리 설정 파일에만 있고 UI가 없던 것을 일관되게 추가. `/api/config` `auto_refresh` 저장.
  **토큰 상태 배지**(유효 N분 남음 / 만료·자동갱신으로 복구 가능 / 재로그인 필요) — 실측이 왜 멈췄는지
  설정 화면에서 바로 보이게. `limits.claude_token_status()`는 **읽기 전용**(build_state가 7초마다 부르므로
  refresh를 걸면 리프레시 토큰이 계속 회전 → 절대 금지, 주석 명시).
- **최근 런 목록 제한**(측정 근거: 12런에 2098px = 화면의 2.8배, 서버 상한 20이면 ~3500px):
  대시보드는 `DASH_RUNS_MAX=8`(관례 5~10)만 렌더 + `#runs{max-height:420px;overflow-y:auto}` 내부 스크롤
  + "N개 더 있음" 힌트. **서버 상한 20은 유지**(콘솔 작업별 그룹핑이 사용). 결과: 2098→420px(0.57배).
- 토큰 확인(요청): 액세스 토큰 만료는 refresh_token으로 **재로그인 없이 자동 재발급**(회전된 토큰도 저장).
  단 **리프레시 토큰 만료(2026-08-12)·4xx invalid_grant 시엔 재로그인 필요**. 미해결 관찰:
  `_write_oauth_atomic`이 `refreshTokenExpiresAt`를 저장하지 않아 상태 배지가 낡은 만료일을 볼 수 있음(표시만).

## 미출시(dev) · 2026-07-16 — [로드맵 3 / C-2] 예약·잠금·배치승인 (codex 구현·Claude 검토)

- 병렬의 **안전 토대**. 스레드는 아직 미도입(C-3).
- `yok3x/reserve.py`(신규, 의존성0): 프로세스간 **lockfile**(`O_CREAT|O_EXCL` 원자획득 + stale TTL 회수 —
  GUI·CLI 동시 실행 경쟁 방지) + **예약 원장**(`reservations.json`, 원자적 쓰기) + `reserve/release/cleanup_stale`.
- **TOCTOU 방어**(codex 최우선 지적): 개별 호출은 가드를 통과해도 **합산이 hard limit를 넘으면 예약 거절**.
  실측 확인 — calls 각 150(<200) 통과·합 300>200 → 2차 False / USD 각 $3(<$5)·합 $6>$5 → 2차 False.
- `approve_batch()`: **제어 스레드에서 1회 배치 승인**(worker thread `input()` 데드락 방지).
  `reserve_and_approve()`: 승인 거부 시 **예약 해제**(누수 없음). `CallSpec.batch_approved`.
- 설정 `guard.reservation`(TTL·락대기·토큰추정비율·hard_limits)로 분리(RULE §5.5). **calls=정확 예약,
  토큰·USD=보수적 추정**임을 코드 주석에 명시.
- 검토: 적대적 프로브 6종 통과(중복락 차단·stale락 회수·calls/USD TOCTOU 거절·release 후 재예약·
  cleanup은 stale만). 스레드 코드 0 확인. **125 passed**(신규 11). `.pytest-tmp*/` gitignore 추가.

## 미출시(dev) · 2026-07-16 — [로드맵 3 / C-1] 준비·실행 분리 + 원자적 쓰기 (codex 구현·Claude 검토)

- 진짜 병렬(C)의 **토대**. 동시성은 아직 도입 안 함(순수 리팩터, 동작 불변).
- `CallSpec` + `prepare_call()`(라우팅·모델·프롬프트·run_cwd 결정, **부작용 0·결정적**) /
  `execute_call()`(단계번호·가드·게이트·실행·기록) 분리. `call_worker`=조합이라 기존 호출부 무수정.
  → 병렬 시 **결정·승인은 제어 스레드**에서 끝내고 worker thread는 실행만(= `input()` 데드락·경쟁 방지).
- `_atomic_write_json()`: 같은 dir 임시파일→`os.replace`. `status.json`·`acquire.json`에 적용(동시쓰기 손상 방지).
- `_isolated_cwd_path()`로 경로결정/디렉터리생성 분리(prepare의 부작용 제거).
- 검토: 적대적 프로브 4종 통과(부작용0·결정성·필드반영·원자성/잔여물없음), 동시성 코드 0 확인, **114 passed**(신규 6).

## 미출시(dev) · 2026-07-16 — [로드맵 2 / ACQUIRE S3a] Resolver 수정 전 QA 재검증 (codex 구현·Claude 검토)

- QA 오고정 회귀(논문 5건) 완화. **LLM 호출 증가 0** — 파일시스템 기계검증이라 무료·결정적.
- `acquire.core_claim()` 결정적 SHA-1 claim_id + 핵심주장. `orchestrator.verify_evidence()`가 evidence의
  path 존재·symbol 등장을 호스트에서 확인(행번호는 미검증 — 논문상 세부오류 대부분이 행번호).
- `apply_verdicts()`: path없음→contradicted(폐기) / symbol없음→partial(위치힌트만) / 둘다→confirmed.
  render는 contradicted 제외·partial 명시. acquire.json에 판정·dropped 이유 감사 저장. 실패 시 fail-safe.
- 검토: 적대적 프로브 4종 통과(claim_id 결정성·경로/심볼 판정·verdict 규칙·렌더 제외), **108 passed**(신규 7).
- 부속 판단: CLIP linear-probe 문의 → 문자 그대로는 부적용(표현공간 없음). 교훈("평가자를 객관신호로 검증")만
  이식 — F0에 **심판 캘리브레이션**(SCORE vs verify_cmd 상관) 추가, 선형모델은 데이터 축적 후 stdlib로.

## 미출시(dev) · 2026-07-16 — [로드맵 1] A-lite 채팅 텔레메트리 (관찰가능성, 사용자 요청)

- 체크리스트 v4.3.0의 1번(G5). 채팅 스텝 카드에 **에이전트별 계측 배지** `⏱작업시간·🔢토큰·💲비용` +
  런 메타 `done/steps 스텝`. StepLog에 nullable `tokens/cost_usd/duration_ms` 추가(0/누락→None=미측정 구분,
  GUI '—'). status.json→build_state→GUI로 흐름. 시간=항상, 토큰/비용=백엔드 보고 시만. **가짜 진행바 없음.**
- 검증: 101 passed, 측정/미측정 배지 렌더 확인(측정: ⏱8.3s·🔢50K·💲0.120 / 미측정: ⏱15.2s·🔢—).

## 계획 · 2026-07-16 — 통합 실행 로드맵 체크리스트 (codex 공동검토, 사용자 요청)

- `plans/v4.3.0-plan-roadmap-checklist`: 흩어진 백로그(리뷰 G1~G5·ACQUIRE S3/S4·부속)를 단일 우선순위
  체크리스트로 통합. codex 검토 반영 — **공통 실행상태 계약 선정의** + 순서 `F0→A-lite→B→C→F→E→G→D→H`.
  C(병렬)의 동시성 함정 10여 개(TOCTOU·예약·lockfile·취소·all-settled) 명시. 이 파일 기준으로 진행.

## 문서 · 2026-07-16 — 멀티에이전트 적정성 리뷰 리포트 (사용자 요청)

- `reports/v4.x-review-multiagent-adequacy`: 기반기술 적정, 다음 티어 격차 5개(G1 병렬성·G2 워커도구·
  G3 앙상블·G4 체크포인트·G5 관찰가능성). 부속: effort ultracode=별도 모드(effort 아님), temp/top_p/top_k=
  코딩 CLI 미지원(우리 누락 아님), few-shot=도입 권장, 채팅 토큰/시간/비용 표시=데이터 있어 구현 쉬움.
- 권장 우선순위: ①관찰가능성 노출 ②병렬 fanout+앙상블 ③few-shot. temperature류 "안 먹는 노브"는 최후순위.

## 미출시(dev) · 2026-07-16 — [ACQUIRE S2] 오케스트레이터 preflight 연동 (codex 구현·Claude 검토)

- `Orchestrator.acquire_preflight()`: Questioner→질문별 Answerer(**read-only**)→validate→유효 QA만 수집→
  `render_qa_context`→첫 단계 `initial_context`로 1회 선주입. 실패/가드stop 시 빈 컨텍스트로 **본 수리 안 막음**.
- **QA는 knot 아닌 `.yok3x/runs/<id>/acquire.json`(일시 메모리)에만** 저장 — BUG-13 자기오염 방지(실호출 없음 검증).
- 호출별 `read_only` 프로필: claude=`--disallowedTools Edit,Write,MultiEdit,NotebookEdit`(Read/Grep 허용·Write 차단),
  codex=`--sandbox read-only`, gemini=`--approval-mode plan`. `call_worker(read_only=)`·`backends`·`config` 배선.
- task spec `acquire:{questioner,answerers,qa_count}` opt-in. 없으면 기존 흐름 무변경(회귀 테스트 확인).
- 구현=codex CLI(생산자), 검토=Claude(리뷰어). 적대적 확인(read-only argv 교체·knot 미호출) 통과, **101 passed**(신규 7).

## 문서 · 2026-07-16 — docs/ 유형별 분리(plans/reports) + RAG·TOOL·메모리 평가 (사용자 요청)

- **plans/ 분리**: 계획서 13개(`vX.Y.Z-plan-*`)를 `docs/reports/` → `docs/plans/`로. reports/는 회고
  (평가·분석·감사·리뷰) + `bugs/` 유지. **HISTORY는 단일 파일 유지**(방대해지면 롤링 아카이브, 항목 분할 X).
- RULE §7.2 신설(문서 폴더 구조·유형→위치 매핑). ADOPTIONS 참조에 `plans/`·`reports/` 접두어.
- 평가 리포트 추가: `reports/v4.x-assessment-rag-tools-memory-2026-07-16.md` — 별도 벡터 RAG=미채택(knot이
  이미 의존성0 RAG-lite), TOOL=MCP 로드맵+ACQUIRE read-only 프로필, 메모리=2계층 이미 존재(분리+수동 승급 핵심).

## 구조 · 2026-07-16 — 저장소 평탄화 + docs/ 통합 (사용자 요청)

- **3겹 중첩 제거**: `test_workspace/yok3x/yok3x/yok3x/`(패키지)를 한 칸 올려 **git 루트 == 프로젝트 루트**로.
  이제 `.git`과 `pyproject.toml`이 같은 층, `yok3x`는 표준 2겹(프로젝트/패키지). `git mv`로 이력 보존(72 rename).
- **docs/ 신설**: `RULE.md`·`HISTORY.md`·`ADOPTIONS.md` → `docs/`. `reports/`(옛 루트 v2.3~3.1 11개 +
  구 `yok3x/reports/` 현재분) → `docs/reports/`로 병합(파일명 무충돌, `bugs/` 포함). README는 pyproject
  `readme=` + GitHub 관례로 **루트 유지**. `backup/`·`release/`·`test_samples/`는 아티팩트라 루트 유지.
- 참조 갱신: `launch.json`(`yok3x/yok3x.py`→`yok3x.py`), RULE §5.5·§7.1·§8 경로 문구, 메모리(버그리포트 경로).
  pyproject는 전부 상대경로라 **무수정**(packages=["yok3x"]·testpaths=["tests"]·readme 그대로 동작).
- 검증: `import yok3x` OK(3.6.0) · `python -m pytest` **90 passed**(새 루트) · 런처 동작. 사전 백업:
  `test_workspace/yok3x-backup-pre-restructure-20260716_144033.tar.gz`(git 이력 포함).
- HISTORY의 과거 항목에 적힌 옛 경로(`reports/…`)는 당시 기록이라 **그대로 보존**(현재 위치는 `docs/reports/…`).

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

## 미출시(dev) · 2026-07-16 — 사용량 스트립 창별 색상·7d 예산눈금·claude effort 확장 (사용자 요청)

- **[BUG-20] 창별 색상**: 5h가 warn(노랑)이면 툴 전체 level로 7d·Fable까지 노랑이 되던 버그.
  `wlvl(used%,guard)`로 창 자기 % 기준 색칠(대시보드 게이지와 동일 원리).
- **[BUG-20] 7d 예산 눈금**: `budgetRuler()` — cap_pct(주간의 하루치, 기본 14%) 배수마다 세로 눈금
  (14%[강조]·28·42…). 페이싱 on/off 무관 항상. 콘솔 스트립+대시보드 7d에 적용(주간 창만).
- **claude effort 확장**: claude CLI가 직접 알려준 유효값 `low/medium/high/xhigh/max`로 정정
  (엑스트라=xhigh, 최대=max 누락분 추가). **ultracode는 effort 아님**(claude가 'Unknown'으로 무시 —
  클라우드 워크플로 기능)이라 드롭다운 제외. guiserver 검증도 max 허용. 90 passed.

## 미출시(dev) · 2026-07-16 — [ACQUIRE S1] acquire.py 순수 모듈 (codex 구현·Claude 검토)

- ACQUIRE(Know-Before-Fix) 이식 1단계: `yok3x/acquire.py` 순수 모듈(의존성0: json·re).
  Questioner/Answerer 프롬프트 빌더, 관대한 JSON 추출(`raw_decode`), QA 스키마 검증
  (evidence·unknowns 강제, 정직한 unknown 예외, confidence 교정), Resolver 컨텍스트 렌더(max_chars 준수).
- **역할 분담 도그푸딩**: codex CLI가 구현(생산자), Claude가 검토(리뷰어).
- 검토: 범위 준수(모듈+테스트만), 적대적 프로브 6종 통과(코드펜스 파싱·unknown예외·근거없는단언 거부·
  confidence교정·max_chars·파싱실패 안전), 단위테스트 16개, **90 passed**(기존 74 유지). 계획서 v4.2.0.

## 미출시(dev) · 2026-07-16 — 콘솔 단일 컬럼(한줄배치) 복구 (사용자 요청)

- BUG-19 수정으로 그동안 blowout에 가려져 있던 **우측 '에이전트 배치' 2컬럼이 드러나자**, 사용자가
  ①좌우 분할 ②좁은 배율 ③하단 빈공간(짧은 우측 패널이 그리드 stretch로 늘어남)을 지적.
- `.cols`를 `grid(1.5fr/1fr)` → **`flex-direction:column`**(단일 컬럼)으로 변경: 작업 콘솔 풀폭(949px) 위,
  에이전트 배치 아래로 세로 적층. 각 패널 자연 높이라 stretch 빈공간도 사라짐.
- 검증: 두 패널 풀폭·세로 적층·채팅 말풍선 넓게 정상. JS 에러 없음.

## 미출시(dev) · 2026-07-16 — [BUG-19] 콘솔 채팅 세로→가로 붕괴 근본수정 (사용자 요청)

- **재현·근본원인 규명**: 채팅이 가로로 붕괴되는 버그를 브라우저에서 재현. 원인은 **에이전트 산출물
  (summary/task)에 든 HTML 태그가 이스케이프 없이 `innerHTML`에 삽입**되어 DOM이 파괴된 것
  (계산기 작업이 만든 `<button>`/`<div>` 코드가 summary에 노출 → `.you`/`.cstep` 상호 중첩 붕괴).
  → "전전 상태에선 멀쩡" = 그때는 `<`/`>` 포함 런이 없었기 때문(라이브 확인: summary 5건에 태그 포함).
- **수정**: `esc()` HTML 이스케이프 추가 → 콘솔·대시보드 런·필터의 **모든 동적 텍스트**에 적용(근본 차단).
  방어로 `.cols`를 `minmax(0,..)`, `.cstep .cb{min-width:0}`, 긴 토큰 `overflow-wrap:anywhere`.
- 검증: 중첩 DOM 0·오버플로 0·가로스크롤 제거(496=496)·세로 적재 복구. 리포트 BUG-19.
- (정정) 직전 커밋의 "가로 버그 재현 안 됨"은 오판이었음 — 실제 재현·수정 완료.

## 미출시(dev) · 2026-07-16 — 작업 CRUD UX: 저장작업만 열기/실행/삭제 활성 (사용자 요청)

- 사용자 혼란("무제목 삭제가 안 눌린다") 해소: 열기/▶실행/🗑 버튼은 **💾 저장된 작업**이 선택됐을 때만
  활성, '무제목(런 라벨)·전체'에선 **비활성(흐리게)**. `syncTaskButtons()` + `.btn[disabled]` CSS.
- CRUD 자체는 정상 동작 검증완료(저장→💾 표시→삭제→목록에서 사라짐, JS 에러 없음). '작동 안 함'은
  UX 오해(무제목=저장작업 아님 / 새 작업 저장은 목표 입력 필요)였음.
- **UI 가로 버그**: 현재 코드에서 재현 안 됨 — `#console-log`은 display:block, 자식(.you/.cstep) 세로 적재
  확인(top 460→526→593…). 최근 작업관리 통합 커밋에서 해소된 것으로 보임(재발 시 화면폭 제보 요청).

## 미출시(dev) · 2026-07-16 — 7일 바 '하루 페이싱 예산' 눈금선 (사용자 요청)

- 7d 사용량 바 위에 **오늘 하루 페이싱 예산 구간**을 시각화: 아침 기준선(점선)→오늘 상한(cap, 실선)
  사이를 초록 띠로, soft 경고선(파선)도. 주간 한도(가로축) 대비 하루에 얼마나 갉아먹는지/남았는지 한눈에.
- 라벨 `오늘 N%p / 상한 M%p` 추가. 페이싱 켜진 실측 백엔드(claude·codex)에서 표시(주간 API 없는 gemini 제외).
- guiserver: pace 상태에 `start`(아침 기준선)·`soft` 노출. GUI: `paceMarks()` + `.mk.pband/pstart/psoft/pcap`.
- 74 passed. codex 7d에서 band+기준선+상한 렌더 확인.

---
