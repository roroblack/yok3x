# TODO — 대기 중인 점검 항목

## 지금 열린 작업 (2026-09-06 기준 · 상세는 각 항목 참고)

| # | 작업 | TeamFlow | 상태 / 막힌 이유 |
|---|---|---|---|
| 1 | **V-11 설정 화면 UI** — `switch_before_degrade`·`failover_min_gain`·계정군 조정 | YOK-86 | **codex 쿼터 소진(stop)으로 대기.** RULE상 UI는 codex 구현·Claude 검토라 직접 안 씀. 복구 시 착수 |
| 2 | **V-11 실계정 e2e 검증** | YOK-87 | 사용자의 두 번째 계정 준비 필요. `limits.<alt>.projects_dir`도 함께 지정해야 쿼터 추적까지 분리됨 |
| ~~3~~ | ~~**MCP 토큰 배선 고정**~~ | YOK-88 | **해결(2026-09-06, `c830c00`)** — 등록을 `~/.claude.json`(git 미추적)으로 옮기고 거기 토큰을 명시. 계정 귀속 관련 종전 기술은 정정됨 — 아래 "운영 위험" 참고 |
| 4 | **V-6 스모크 테스트 이후 확장** | YOK-89 | 사용자가 "나중에"로 보류 |
| 5 | T-4(a2)·T-6·V-5§4.2·V-8·V-9 확장 | YOK-90·91·92·93·94 | 전부 데이터·실사용·전제조건 대기(각 항목 참고) |

> 이 표의 각 항목은 TeamFlow(프로젝트 YOK)에 `todo` 상태로도 등록돼 있다. 작업 이력 전체
> (에픽 6·스프린트 3·일자별 태스크 21·디버그노트 54)도 2026-09-06에 백필했다 — RULE §9.

### 운영 위험 — MCP 토큰 배선 (2026-09-06)

[`docs/reports/v4.x-assessment-teamflow-token-scope-2026-09-06.md`](reports/v4.x-assessment-teamflow-token-scope-2026-09-06.md) ·
TeamFlow [YOK-4] · [YOK-88]

**배선 자체는 정리됐다.** `.mcp.json`은 git 추적 파일이라 토큰을 두지 않고, 등록을 개인 전역
설정 `~/.claude.json`(git 미추적)으로 옮겨 `env.TEAMFLOW_API_TOKEN`에 명시했다(`c830c00`).
`env.js` 33행이 클라이언트가 넘긴 값을 `.env`보다 우선하므로, 공유 `.env`가 바뀌어도 이쪽은
안 흔들린다.

**계정 관계**: 양쪽은 서로 다른 계정이며 각자 자기 프로젝트에만 속한다는 것을 DB 조회로
확인했다(상세는 `docs/private/` — 저장소 미포함).

**방법에 대한 정정**: 종전 진단서는 결론은 맞았지만 **근거가 추론**이었다. `list_projects`는
토큰이 닿는 프로젝트만 알려줄 뿐 **소유 계정은 안 알려준다**.

**아직 유효한 오류 — 책임 귀속의 방향**: "저쪽 배선만 빠졌다"는 근거가 없다. 공유 `.env`에
이쪽 토큰을 넣은 건 이쪽이다. 한쪽만 지목한 게 오류다. 폴백도 버그가 아니라 의도된 설계다.

**교훈**: `list_projects`는 "닿는 프로젝트"를 알려주지 "누구인지"를 알려주지 않는다. 소유 계정을
범위로 추정해 결론 쓰지 않는다. (TFDEV 쪽에서 `whoami` 도구·시작 시 신원 로그를 제안 중 —
있었으면 양쪽 다 첫 호출에서 알았다.)

---

데이터·시간이 쌓여야 판단 가능한 항목을 여기 모은다.
점검은 **수동 실행**: `python scripts/todo_check.py` — 맨 아래 '자동 점검 상태'를 갱신한다
(읽기 전용 계측, 판단은 사람이). 지금은 데이터가 0이라 실제 런이 쌓이기 전엔 결과가 안 바뀐다.
자동 스케줄은 걸지 않았다(데이터가 쌓이기 시작하면 그때 스케줄 여부 재검토).

---

## T-1. 심판 캘리브레이션 검증 — **mutation-testing 파일럿으로 사실상 해결(2026-08-30)**

**최종 결론** — [`docs/reports/v4.x-result-t1-mutation-testing-pilot-2026-08-30.md`](reports/v4.x-result-t1-mutation-testing-pilot-2026-08-30.md):
원래 착수 조건(`verify_ok=False`≥3)이 구조적으로 안 나오는 문제(위 진단 참고)를, producer의
자연스러운 실패를 기다리는 대신 **정상 코드에 확정된 결함(mutation)을 인위적으로 주입**해
해결했다(2026-08-30 기준 웹 리서치: SWE-ABS/SWE-Mutation 등 2026년 논문이 쓰는 표준 기법 +
codex와의 설계 토론으로 K/S 혼합·witness·사전등록 방법론 확정). 대상 2개(#12·#11)에 각각
K(단순)+S(semantic) mutant 1개씩 총 4개를 witness로 검증 후 주입, 기존 pytest로
verify_ok 확인, 원본 2개+mutant 4개를 전부 새 세션·블라인드·순서혼합으로 재심사.

**결과 — 사전등록 기준 6개 전부 충족**, 특히 "심판이 주입 결함을 구체적으로 지적" 기준은
최소 3/4을 넘어 **4/4 만점**(injected bug를 문장 단위로 정확히 서술). 가장 결정적인
증거: #12의 S mutant는 기존 테스트가 완전히 놓친(verify_ok=True) 진짜 spec 위반 결함이었는데,
codex가 이를 **critical**로 정확히 짚어냈다 — "심판이 테스트가 못 보는 곳까지 실제로 본다"는
통제된 직접 증거. 표본은 작지만(대상 2개·mutant 4개) 사전등록·witness·블라인드를 전부
지킨 실험에서 4/4는 우연으로 보기 어렵다.

**권고**: 이 방법론(mutation+witness+사전등록+블라인드)을 T-1의 정식 캘리브레이션 절차로
채택. verify_ok=False를 기다리는 것보다 훨씬 저비용(전체 codex 호출 8회 미만)·통제 가능.
V-1·V-5§4.2를 막고 있던 "T-1 미해결" 전제조건은 이 결과로 재검토 가능한 상태가 됐다 —
다만 표본 확대(사용자 승인 시) 여부는 별도 결정 필요.

---

## T-1(구). 심판 캘리브레이션 검증 (데이터 대기 중 — 아래는 원래 착수 조건 기반 접근, 위 파일럿으로 대체됨)

**진단 완료(2026-08-30, 비용 0 — 기존 산출물 재분석)** —
[`docs/reports/v4.x-result-t2-round2-verify-ok-false-diagnosis-2026-08-30.md`](reports/v4.x-result-t2-round2-verify-ok-false-diagnosis-2026-08-30.md):
`verify_ok=False`가 왜 한 건도 안 나오는지 실제 산출물로 추적. 결론 — **리뷰어(codex-critic)가
미보정된 게 아니라 오히려 정확했다**: #11·#12의 1라운드 저점(SCORE 6.0)에서 리뷰어가 지적한
구체적 결함 3건을 사후 프로브로 재현한 결과 **3/3 전부 실제로 재현됨**. 그런데 이 3건 전부
**내가 사전에 손으로 짠 참조 구현에도 똑같이 존재**했다 — 즉 결함의 근원은 producer가 아니라
**내가 쓴 자연어 스펙 자체의 빈틈**이었고, 그 빈틈이 테스트(내가 같이 씀)에도 그대로
상속되니 producer가 그 테스트를 통과하는 한 `verify_ok=False`가 될 수 없는 구조였다.
`orchestrator.py` 코드 추적으로 "테스트가 producer에 노출돼서"가 아님도 확인함(`context_globs`
기본 빈 배열 — producer는 애초에 어느 라운드에서도 테스트 파일을 본 적이 없고, 1라운드부터
이미 blind 상태로 verify_ok=True를 냈다) — **"hidden test 도입" 옵션은 이미 사실상 적용
중이라 폐기**. 남은 옵션은 (1) **스펙·테스트를 서로 다른 주체가 작성**(근본원인을 직접
없애는 유일한 옵션 — 권고, 소규모 파일럿부터), (2) T-1 정의 자체를 "리뷰어 결함 적중률"로
재정의(1번과 병행 가능, 비용 거의 0). 3차 프로토콜을 같은 방식(동일인이 스펙+테스트 작성)
그대로 반복하면 같은 이유로 또 0건이 나올 가능성이 높음 — 실행은 안 함(사용자 승인 필요).

**파일럿 완료(2026-08-30, 사용자 승인, 비용 미미 — codex 단발 호출 2회)** —
[`docs/reports/v4.x-result-t1-pilot-spec-test-separation-2026-08-30.md`](reports/v4.x-result-t1-pilot-spec-test-separation-2026-08-30.md):
옵션 1(스펙·테스트 작성자 분리)을 실제로 시도. **#12는 가설 기각** — 완전히 독립된 codex
세션이 같은 스펙만 보고 짠 테스트가, 리뷰어가 "high severity"라 지적한 바로 그 동작을
"스펙과 일치함"이라고 명시적으로 정답 처리했다 — 즉 그 결함은 개인 blind spot이 아니라
**스펙 텍스트 자체의 진짜 모호함**이라 작성자를 바꿔도 안 잡힌다. **더 중요한 예상 밖
발견**: 같은 리뷰어(codex-critic)가 **행동상 동일한 코드**(round-1 저점 후보와 round-2
게이트-통과 후보가 이 결함에 관해 정확히 같은 동작임을 독립 테스트+재현 테스트로 이중 확인)를
두고 round-1에서는 "high severity, 반드시 수정" → round-2에서는 "스펙과 일치, 문제 없음"으로
**정반대 판정**을 냈다 — producer가 고친 게 아니라 리뷰어가 스스로 재판정을 바꾼 것. 이건
T-1이 원래 노렸던 "SCORE↔verify_ok 상관관계"보다 더 직접적으로 "심판이 라운드 간 자기
일관성이 있는가"에 부정적 증거를 준다(단, n=1이라 일반화는 이름).

**전수 확인(2026-08-30, 비용 0)**: 보존된 raw 산출물이 있는 다라운드 사례 4건을 전부
훑어봄 — 1차 slugify(7.5→9.0)·1차 calc(7.0→8.2)·2차 worker pool(6.0→4.0) **3건은 전부
정상**(코드가 실제로 바뀐 만큼 결함 목록도 합리적으로 진화, worker pool은 오히려 새
critical 결함이 드러나 점수 하락 — 이것도 정당함). 재판정 불일치는 **4건 중 1건(incremental
builder)**에서만 관측 — 흔한 패턴이 아니라 드물게 실제로 일어나는 유형으로 보임.

**분산 실험 완료(2026-08-30 야간, 사용자 승인 "코덱스 최대한 써서", codex 단발 호출 총 20회)** —
같은 문서에 결과 추가. 코드만 넣고 6회씩 재심사했을 땐 #12 round-1이 9.08±0.19로 매우
안정적으로 나와("라운드 맥락이 판정을 바꿨다"는 서사를 뒷받침하는 듯 보였으나), producer의
**원문 그대로**(계획+코드+SELF-CHECK, 실제 오케스트레이션이 봤던 그대로)를 넣어 4회씩
재심사하니 완전히 다른 그림이 나왔다 — **5.0, 8.5, 8.5, 9.0(stdev 1.6)**. 원래 round-1의
6.0은 이 넓은 분산 범위 안에 자연스럽게 들어간다. **최종 결론이 수정됨**: "리뷰어가 라운드
맥락에 따라 재판정을 바꿨다"보다 **"SCORE 자체가 완전히 동일한 입력에도 큰 고유 분산을
가진 노이즈 많은 신호"**라는 설명이 데이터와 더 잘 맞는다(단 #8은 반대로 원문 재현에서
stdev=0으로 매우 안정적이었다 — 분산의 크기 자체가 사례마다 다르다는 뜻, n이 각 4뿐이라
잠정적). **T-1에 대한 실질 함의**: SCORE↔verify_ok 상관관계를 따지기 전에, 라운드당 1회
관측만으로 SCORE를 신뢰하기 어려운 사례가 실제로 있음을 확인했다 — 향후 캘리브레이션은
리뷰당 다회 반복(비용 N배 트레이드오프, 별도 승인 필요)을 고려해야 함.

**해결 시도 완료(2026-08-30, 사용자 지시 "웹리서치·codex 토론으로 해결")** — 같은 문서에
전체 기록. (1) 웹 리서치: 이 현상은 학계에도 알려진 문제(EMNLP 2025 "Rating Roulette" 등),
temperature=0은 나쁜 트레이드오프, 검증된 완화책은 "다회 실행+다수결". (2) codex와 직접
설계 토론(내 "점수 먼저 쓰는 순서가 원인" 가설에 반박 요청) — codex가 내가 못 찾은 근거
논문(Chen et al., 순서가 평균 점수엔 영향 주지만 분산을 항상 줄이진 않음)까지 찾아와
반박하며, **"결함 탐지 자체의 불안정성"과 "점수 환산 방식"을 구분해야 한다**고 지적,
LLM은 구조화 결함 목록만 내고 SCORE는 오케스트레이터가 결정론적 공식으로 계산하라고
제안. (3) **이 제안을 이미 가진 데이터로 추가 비용 없이 즉시 검증** — 고분산 사례(#12
round-1, #8)는 결정론적 재계산으로도 분산이 줄지 않음(입력인 결함 탐지 자체가 매번
다름), 저분산 사례(#9)는 확실히 개선(stdev 0.22→0.04). **결론**: 점수 계산 결정론화는
필요하지만 충분하지 않음 — codex가 미리 경고한 정확히 그 지점이 실측 확인됨. **최종
권고**(구현 전, 승인 필요, 실행 안 함): ① T-6(구조화 리뷰, 현재 관측만) 확장해 SCORE를
결함목록 기반 결정론적 계산으로 전환(저위험, 저분산 사례 개선), ② critical/high 결함
존재·게이트 임계값 근처인 고위험 사례에만 조건부 2·3차 블라인드 재리뷰(점수 평균 아닌
결함 단위 재현 확인) 추가.

**계획서 승격 및 옵션 ① 구현 완료(2026-08-30)** —
[`docs/plans/v4.x-plan-deterministic-review-scoring-2026-08-30.md`](plans/v4.x-plan-deterministic-review-scoring-2026-08-30.md):
`review_protocol.compute_deterministic_score()` + `cfg.yok3x["review_protocol"]`(기본
off, opt-in) + orchestrator 라운드 루프 배선으로 구현. 기본 off라 켜기 전까진 기존 동작
100% 불변(회귀 테스트로 확인), 단위 테스트 9개·통합 테스트 3개 전부 통과. 옵션 ②(조건부
블라인드 재리뷰)는 round-loop 재시도 로직과 얽혀 더 큰 변경이라 계속 보류.

### 배경
- **N0′**(2026-07-20): 심판(codex-critic)은 명백한 결함을 원본보다 낮게 평가한다(win 17/20, p=0.0001).
  **그러나** 제품 게이트 임계 **8.0**에서는 정상 코드조차 전량 반려됐다(정상 최고 SCORE 7.0).
- **작업B**: (SCORE, verify_ok) 실데이터 수집을 강화(라운드별 기록·스키마 확장).
- **작업E**: 리뷰어가 verify 결과를 **본 뒤** 채점하던 **라벨 누출**을 제거(blind 채점).
  **단, 작업E가 실제로 개선인지는 아직 미검증**(효과 측정 0).
- **F1-f**: `file:` 후보가 있으면 workdir 격리 사본에 후보를 적용해 verify하고
  `verify_scope="candidate"`로 기록한다. 후보가 없거나 스테이징 준비가 실패한 관측만
  `original_tree`로 남아 라벨에서 제외된다.

### 데이터가 쌓이면 할 것 (순서대로)
1. `yok3x calib --threshold 8.0` 으로 SCORE↔verify 상관·혼동행렬 확인.
2. **작업E A/B 검증**: blind 리뷰어(현재) vs 누출 리뷰어(이전)의 상관을 비교해
   E가 (SCORE↔verify) 관계를 실제로 바꿨는지 측정. → 커밋 메시지의 "개선" 주장 유지/철회 결정.
3. **게이트 임계 재설정**: 실데이터 기준으로 8.0을 조정할지 별도 사전등록 실험으로 판단
   (N0′의 사후 최적값 t=4는 합성·사후라 그대로 채택 금지).

### "충분한 데이터" 기준 (사전 고정 — 결과 보고 바꾸지 않음)
선행조건인 후보 스테이징(**F1-f**)은 완료됐다. `calibration.jsonl` 중
`verify_scope="candidate"`인 `verify_ok`만 SCORE↔verify 지상진실 라벨로 사용한다.

| 기준 | 값 | 근거 |
|---|---|---|
| 독립 런(run_id) 수 | **≥ 10** | `calibration.summarize`의 n<10=표본부족. 행이 아니라 **독립 런**으로 셈(한 런의 여러 라운드는 상관·codex) |
| verify 통과 관측 | **≥ 3** | 한쪽뿐이면 상관·혼동행렬이 정의 안 됨 |
| verify 실패 관측 | **≥ 3** | 위와 동일(양쪽 다 있어야 퇴화 안 함) |

F1-f 선행조건과 세 조건을 **모두** 충족하면 점검 착수. 유효 데이터는 후보 스테이징 후
`verify_cmd`가 설정된 실제 producer-reviewer 런에서만 쌓인다.

### 중단·주의
- 임계 재설정은 **실 verify 신호로만**. 합성 변이(N0′ 40개)로 정하면 CLIP 교훈 위반.
- 데이터가 충족돼도 "점수 1점 높음"류로 성급히 결론짓지 않는다(계획서 합격선 참조).

---

## T-2. 실 캘리브레이션 데이터 수집 런 ⚠️ **쿼터 소모 — 사용자 승인 시에만**

> **1차 수집 완료(2026-07-27, 12런 $8.10)** — [결과](reports/v4.5.0-t2-collection-round1-results-2026-07-27-1240.md).
> 독립런 12 ✅ · verify 통과 14 ✅ · **verify 실패 0 ❌(기준 3)** → **T-1 착수 불가, 2차 수집 필요.**
> 2차는 **난이도를 확실히 올린 작업군**으로(현 작업군은 12/12 통과 = 너무 쉬움). 별도 사전등록 필요.
> N0′ 우려는 재현됨(통과 코드 21%가 점수 미달 반려). 임계 재설정은 tn=0이라 여전히 보류.
>
> **수집 방식(2026-07-27 사용자 결정): 하루에 일정량씩 나눠 진행한다.** 한 번에 몰아 돌리지 않는다 —
> 1차에서 12런에 $8.10(중단조건 $10 근접)이 나왔고 그중 2런이 79%를 차지했다. 하루 한도를 정해
> 쿼터·비용 모두 페이싱한다.
> 런당 상한은 `guard.reservation.max_usd_per_run`으로 강제 가능(2026-07-27 추가, 기본 0=off).
>
> **2차 사전등록 완료(2026-08-10, 사용자 승인 + codex 설계상담)** —
> [프로토콜](reports/v4.6.0-t2-protocol-preregistration-round2-2026-08-10.md). 작업군 12개(중간3·
> 어려움6·매우어려움3, codex 설계), `max_usd_per_run=1.50` 신규 적용, 예산초과 런은
> `censored`로 별도 표시(실패 표본 오염 방지).
>
> **진행 상황(페이싱: 하루에 일정량씩, §5.6)**:
> - 2026-08-10: #1(정확한 금액 분배기) `verify_ok=True` $0.4265, #10(동시성 TTL LRU 캐시)
>   `verify_ok=True` $0.4688 — 2/12 완료, 누적 $0.8953.
> - 2026-08-11: #2(유니코드 사용자명 정규화) `verify_ok=True` score=10.0 $0.2275, #3(JSON Patch
>   부분 적용기) 1차 시도는 revise 라운드가 claude CLI 타임아웃(600s)으로 실패해 재시도 →
>   2라운드 모두 `verify_ok=True`(score 7.0→6.0, 반려) $0.69, #4(프레임 프로토콜 파서)
>   2라운드 모두 `verify_ok=True`(score 7.0→4.0, 반려), #5(외부 병합 정렬기) 2라운드 모두
>   `verify_ok=True`(score 7.5→7.0, 반려), #6(이벤트 로그 재생기) task.json에 이스케이프 안 된
>   따옴표로 JSON이 깨지는 실수 발견·수정 후 2라운드 모두 `verify_ok=True`(score 4.0→7.0, 반려),
>   #7(재귀 표현식 평가기) 1라운드 `verify_ok=True`(7.0)까지는 정상, 2라운드(revise)에서
>   claude 응답이 예산 상한(`max_usd_per_run=1.50`)에 걸려($1.664) 중단 — 데이터로 기록 안 되고
>   폐기($1.66 낭비, 아래 "관찰" 참고). 7개 작업 완료/시도, 누적 candidate 관측 14건,
>   **verify_ok=False는 여전히 0건** — "어려움" 등급까지도 claude-main이 테스트는 계속 통과시킴.
> - 2026-08-29: #8(결정적 DAG 스케줄러) `verify_ok=True` score=9.5(1라운드 통과) $1.1737 —
>   3/12 완료(누적 $2.0690). 사용자 승인 후 재개, 프로토콜 §5-6(하루 페이싱)에 따라 오늘은
>   1개만 진행. 남은 작업: #9 구간 자원 할당기, #11 취소 가능 worker pool, #12 증분 빌드기 — 3개.
> - 2026-08-29(계속): 사용자가 나머지 3개(#9, #11, #12)를 한 번에 승인 — §5-6 하루 페이싱
>   중단조건을 오늘만 명시적으로 override. #9(구간 자원 할당기, bisect 기반) `verify_ok=True`
>   score=9.5(1라운드 통과) $0.2658 — 4/12 완료(누적 $2.3348). #11(취소 가능 worker pool)
>   2라운드 모두 `verify_ok=True`(score 6.0→4.0, revise가 오히려 악화돼 반려) $1.4588 —
>   5/12 완료(누적 $3.7936). #12(증분 빌드기, hash+의존성 그래프) 2라운드
>   `verify_ok=True`(score 6.0→9.5, 게이트 통과) $0.6311 — **12/12 전량 완료(누적 $4.4247,
>   $15 한도 내 마감).**
>
> **2차 수집 종료 — 최종 판정**: 12개 작업(중간3·어려움6·매우어려움3) 전량 실행, 독립 런
>   수(≥10) 조건은 충족했으나 **`verify_ok=False`는 1차·2차 통틀어 단 한 건도 관측되지
>   않았다**(T-1 언락 조건: `verify_ok=True`≥3 그리고 `verify_ok=False`≥3). 즉 이 사전등록
>   프로토콜로는 T-1(판정 보정) 언락 조건을 충족시키지 못한 채 종료됨 — "매우어려움" 등급
>   과제까지도 claude-main가 (때로는 재시도를 거쳐) 테스트를 통과시켰다는 뜻이다. 다음
>   조치가 필요하면 둘 중 하나: (a) 3차 프로토콜에서 과제 난이도/함정을 한 단계 더 올려
>   재설계, 또는 (b) `verify_ok=False` 자체가 이 작업군 규모(단일 파일, 명세 기반 유닛
>   테스트)에서는 애초에 드물게 나온다는 것을 받아들이고 T-1 언락 기준 자체를 재검토.
>   사용자 판단 필요 — 현재 세션에서는 추가 실행 안 함(3개 승인분 소진).
>
> **관찰 3 — CLI를 저장소 디렉터리에서 실행하면 태스크별 격리 config가 무시됨(2026-08-29,
>   #8 첫 시도에서 발견)**: `yok3x run <task.json>`을 태스크 workdir가 아니라 **yok3x 저장소
>   디렉터리 안에서** 실행하면, `cli.py`의 `Config.load(".")`가 task.json의 `workdir` 필드가
>   아니라 **CLI 실행 위치(CWD)의 yok3x.json**을 로드한다. 이 때문에 저장소 자체의
>   `active_profile=best`가 끼어들어 **producer/reviewer의 backend가 완전히 뒤바뀜**(claude-main이
>   실제로는 codex로, codex-critic이 실제로는 claude로 실행됨) — 사전등록 프로토콜의 고정 조건
>   위반. 저장소의 `guard.reservation.max_usd_per_run=0`(무제한)도 같이 적용돼 태스크별로 설정한
>   1.50 상한이 무효화됨(실비용은 $0.5648로 다행히 크지 않았지만 원칙적으로 안전장치 없이 실행된
>   것). 이 첫 시도의 관측은 **candidate로 기록하지 않고 폐기**했다(실비용 $0.5648 낭비).
>   **교훈**: T-2 런은 반드시 태스크의 workdir 안으로 `cd`한 뒤 그 안에서 CLI를 실행해야 한다
>   (`workdir` 필드는 orchestrator 내부의 파일 배치용일 뿐, config 로드 경로가 아님) — 이후
>   시도부터는 이렇게 실행해 정상 확인.
>
> **관찰 1 — "거부성 응답 감지" 오탐(2026-08-11, #7 조사 중 발견)**: `orchestrator.py:705-707`의
>   거부 탐지는 응답 앞 200자 안에 `"I cannot"`/`"죄송하지만 할 수 없"` 문자열이 있으면 무조건
>   체크리스트에 표시하는 단순 substring 매칭이다. #7의 revise 단계에서 claude가 "이 환경엔
>   Read/Write/Bash 도구가 없어 코드를 직접 실행해 볼 수 없다(그래서 검증 결과를 지어내는 대신
>   손으로 추적했다)"는 **정직한 disclaimer**로 응답을 시작했는데 여기 `"I cannot"`이 우연히
>   걸려 오탐됐다 — 실제로는 완결된 구현(26,671토큰)을 정상 제출했다. 이 플래그는 순수 로그용
>   표시일 뿐 재시도·비용에 영향 없음(코드로 확인). #7의 비용 초과($1.66)는 이 오탐과 무관 —
>   그 revise 응답 자체가 원래 크고 비쌌던 것뿐. 실제 결함은 아니라 BUG로 등록하지 않음(단순
>   휴리스틱의 알려진 한계) — 향후 이 로그를 다시 볼 때 "진짜 거부가 있었다"로 오독하지 않도록
>   기록만 남김.
> - **관찰 2 — codex 사용량 대시보드가 계속 0%로 보임(2026-08-11, 사용자 문의로 확인)**: T-2가
>   codex를 리뷰어로 계속 실 호출하고 있음을 런 산출물(`step_*_codex-critic.json`)의
>   `usage.total_tokens`(예: 34,550)·`duration_ms`(예: 36,928)·응답 내용(제출 코드의 실제 함수명을
>   구체적으로 지적하는 등)으로 직접 확인 — `"replayed": false`도 확인, 캐시 아님. 그런데
>   `usage.cost_usd`는 매번 `0.0`으로 찍히고 대시보드 7일 사용률도 계속 0%대 — `plan=prolite`가
>   토큰당 과금이 아니라 정액 쿼터 소진형이라 그런 것으로 보이나, GUI 대시보드가 이 플랜의
>   실제 소진율을 정확히 반영하고 있는지는 미확인 — 별도 조사 필요(추측만 하고 결론 내리지
>   않음, `docs/plans/`에 정식 조사 항목으로 등록할 가치 있음).


T-1의 유일한 남은 선행조건. 기계(F1-a~g)는 다 갖춰졌고, **실제 producer-reviewer 런을 돌려야만**
`verify_scope="candidate"` 라벨이 쌓인다. 이건 **실제 LLM 호출(claude/codex 쿼터 소모)**이라
사용자가 "지금 돌려라" 할 때만 실행한다.

- 조건: `verify_cmd` 설정 + `materialize` 또는 `changes:{mode:review}` 켠 producer-reviewer 런.
- 목표량: 독립 런 ≥10 · verify 통과 ≥3 · 실패 ≥3 (T-1 기준).
- 대표 작업군(N0' 방법론): 여러 난이도·유형의 코딩 작업(대표 1개 아님). 같은 base·model·effort 고정.
- 비용 감각: 런당 producer+reviewer×라운드 = 수~수십만 토큰. 수십 런이면 상당량 → **사전에 페이싱 확인**.
- 완료 후 → T-1 착수(`yok3x calib` · 작업E A/B · 임계 재설정).

## T-3. 수정 적용 방식 — auto-commit(ratcheting) vs 파일게시+사람수락 — **완료(2026-07-26, v4.5.0)**

**결정(2026-07-26 · 사용자)**: 택1(auto-commit 전용)이 아니라 **병행** — 설정 스위치로 사용자가 고르게
하고 **기본값은 파일게시+사람수락(현행)**, `auto_commit`은 opt-in.

**구현 완료**: R-7 1·2단계로 함께 구현·릴리스됨(`docs/HISTORY.md` v4.5.0 · 2026-07-26).
- `yok3x/worktree.py`(R-7 1단계): 병렬 워커를 git worktree로 격리(`guard.parallel.worktree_isolation`,
  기본 off). 비-git·실패 시 사유 로그 + 공유 workdir 폴백.
- `orchestrator.py`의 `changes.apply_mode = "review"(기본) | "auto_commit"`(R-7 2단계): auto_commit이면
  verify 통과 라운드를 격리 브랜치(`yok3x/run_<run_id>`)에 체크포인트 커밋, 회귀 시 revert. 비-git이면
  사유 로그 후 review로 자기 비활성화.
- 검증 중 **BUG-39**(cp949 콘솔이 로그의 `—`를 못 그려 크래시 → 체크포인트 유실) 발견·수정.
- 관련 테스트 7개 통과(`pytest -k "worktree or auto_commit or ratchet"`).

**완료**: GUI 작업 편집 폼에서 `changes.apply_mode`를 review/auto_commit으로 선택할 수 있음(기본 review).

## T-4. MCP 워커도구 a2 — yok3x가 직접 MCP 클라이언트로 도구 호출 (2026-08-08 등록, 후속 착수)

**배경**: v4.1.0 계획서의 a1(yok3x가 MCP 서버 설정을 워커 CLI에 전달만 하고, 실행은 워커 CLI 런타임이
직접 함)은 2026-08-08 구현·배포됨(`docs/HISTORY.md`, `yok3x/mcp_policy.py`). 사용자 결정: a1만 먼저
착수하고 **a2는 "차후 고려 후 적용"으로 이 항목에 등록**(2026-08-08).

**a1의 구조적 한계(a2가 필요한 이유)**: yok3x가 개별 도구 *호출*을 가로채지 않으므로(워커 CLI가 직접
실행) — 인자·경로·호스트 단위 실시간 검증, 호출별 예산/타임아웃, "실제로 어떤 도구가 몇 번 호출됐는지"의
감사(a1은 "무엇이 허가됐는지"만 기록)가 전부 불가능하다. codex 리뷰가 "1차 a1(제한적 실험) → 안정화 후
a2 중앙 실행기(통제·감사·재현성 중앙화)로 이관"을 권고한 이유.

**착수 조건**: a1이 실사용으로 충분히 안정화된 뒤(v4.1.0 계획서 §"(a2) 별도 후속" 참고). 구현 전 별도
계획서 + 이 계획서와 같은 수준의 명시적 안전 확인(opt-in·화이트리스트·인자정책·fail-closed 등)이 선행돼야
한다 — a1과 마찬가지로 "텍스트 생산자=안전" 설계를 더 크게 완화하는 변경이라 신중 요구.

**착수 조건 점검 완료(2026-08-29)** —
[검토 결과](plans/v4.x-plan-mcp-a2-central-execution-review-2026-08-29.md): a1이 2026-08-08 배포 이후
**단 한 번도 실사용된 적이 없음**(`mcp_audit.jsonl` 없음, 저장소 `mcp_servers` 화이트리스트 비어있음,
어떤 워커 설정에도 opt-in 없음, 버그 리포트에 MCP 언급 0건 — 유일한 사용처는 단위 테스트뿐)을
확인. "실사용으로 안정화" 조건은 판단할 표본 자체가 없어 **미충족 — 계속 보류**. 누군가 실제로
mcp_servers를 등록하고 워커가 여러 차례 opt-in해 쓴 이력이 쌓이기 전까지 재검토 안 함.

## T-6. 구조화 리뷰 프로토콜(v4.8.0) 파싱 성공률 검토 — 데이터 대기 중

### 배경
- 현재는 구조화 리뷰 파싱을 관측만 하며, 구조화 서명을 기본값으로 강제하지 않는다. 기존 `legacy_text` 폴백은 계속 안전망으로 유지한다.
- `review_protocol_observations.jsonl`에 원문 없이 source·parse error·reviewer별 관측만 누적한다.
- **실측 확인(2026-08-30)**: 이 파일이 메인 저장소 `.yok3x/`뿐 아니라 **T-2 각 task workdir마다
  별도로 흩어져 있다**(calibration.jsonl과 같은 파편화 — 격리 workdir에서 실행하면 그 실행의
  `.yok3x/`에 생기기 때문). 전부 모아 세어보니 codex(리뷰어) 6건·claude(리뷰어) 1건, 전부
  `source=structured`·parse_error 없음. 기준(backend별 ≥20건)엔 둘 다 한참 못 미침 —
  자동 집계 메커니즘이 없어(`sync-calibration`류 명령이 이 파일엔 없음) 앞으로도 수동으로
  모아 세야 정확한 진행률을 알 수 있다.

### 데이터가 쌓이면 할 것
- backend별 독립 관측이 **20건 이상** 쌓이면 파싱 성공률과 false stall/false progress 사례를 검토한다.
- 검토 결과를 근거로 구조화 서명을 기본으로 전환할지 결정하고, 실패 시 legacy 폴백 정책을 재확인한다.
- 구조화 `score`를 게이트의 독립 입력으로 승격할지는 별도 결정으로 다룬다.

**예비 확인(2026-08-30, n=7 — 기준 미달, 참고용·정식 결론 아님)**: 현재 있는 관측 7건(codex 6·
claude 1, 독립 run_id 5개) 전부 `source=structured`·parse_error 없음 — **파싱 성공률
7/7(100%)**. 이 5개 run의 `status.json`도 대조해봄: `success` 3건·`max_rounds` 1건(정상
소진, #11 — 2라운드 다 verify_ok=True였고 실제로 점수가 악화된 케이스)·나머지 1건은 이미
무효 처리된 런(CWD 설정 버그로 폐기, T-2 "관찰 3" 참고) — **false stall(`no_new_evidence`
오탐)은 이 표본에서 0건**. 방향은 긍정적이지만 n=7은 기준(≥20/backend)의 1/3도 안 돼
정식 결론은 못 낸다 — 특히 claude가 리뷰어인 경우는 n=1이라 사실상 무의미.

### "충분한 데이터" 기준 (사전 고정)

| 기준 | 값 | 근거 |
|---|---:|---|
| backend별 독립 관측 | **≥20건** | backend별 파싱 성공률·false stall/false progress를 따로 검토하기 위한 최소 표본 |

---

## T-5. (다른 대기 항목이 생기면 여기에 추가)

---

## 비전(Vision) — 외부 연구/도구에서 발견한 확장 후보 (2026-08-19 등록, 구현 검토 전)

**주의**: 아래는 전부 **미착수, 아이디어 단계**다. 구현 전 각각 별도 계획서(docs/plans/)로
승격시켜야 한다. 여기 있다는 것 자체가 "하기로 결정"을 뜻하지 않는다 — 나중에 훑어보고
가치가 있으면 그때 계획서를 쓴다.

### V-1. 재사용 가능한 "런북" — StateM 하네스 스케일링 (arXiv 2608.15089)

**계획서 승격됨(2026-08-24)**: [`docs/plans/v4.x-plan-reusable-runbooks-2026-08-24.md`](plans/v4.x-plan-reusable-runbooks-2026-08-24.md)
— 검토 결과 착수 조건(T-2 실데이터) 미충족으로 **구현 보류 권고**. 설계 초안·위험·결정 필요
사항은 그 문서 참고.

**제한적(opt-in) 파일럿 구현 완료(2026-08-30)**: 착수 조건이던 T-1이 mutation-testing
파일럿으로 재검토 가능해졌지만, codex와의 토론에서 "4/4 성공만으로는 95% 신뢰 하한이 약
50%뿐"이라는 통계적 반박을 받아 **전면 구현이 아니라 제한적 opt-in 파일럿**으로 범위를
좁혔다. `yok3x/runbooks.py` + `cfg.yok3x["automation"]["use_runbooks"]`(기본 False) —
검증 통과(verify_ok+gate 통과) 라운드만, 고정 태그 어휘(자유 텍스트 금지)로만 기록하고,
1라운드 프롬프트에 "참고, 지시 아님"으로만 주입한다. 실패·반려 접근은 애초에 저장 안
하고, 재사용 횟수가 늘어도 힌트 문구의 확신도는 안 올라간다(codex가 지적한 "탐색 다양성
축소·피드백 루프" 위험 대응). 전면 활성화 기준(대표 작업 15~20개·mutant 30~40개)은
사전 고정, 미충족 시 opt-in 파일럿으로만 존재. 상세:
[`docs/plans/v4.x-plan-reusable-runbooks-2026-08-24.md`](plans/v4.x-plan-reusable-runbooks-2026-08-24.md).

**출처 요지**: 모델 가중치를 안 건드리고 런타임(하네스)만 개선해 성능을 크게 올린 연구
(GPT-5.6 기준 95.3%, 베이스라인 대비 38배 저렴). 5개 구성요소: 지속 상태·**복구 가능한
런북**·절차 제어·단계별 컨텍스트·검증된 전환.

**우리와의 접점**: yok3x는 이미 `.yok3x/runs/<run_id>/status.json`(지속 상태), `yok3x_technique`
require_plan/require_selfcheck(절차 제어 — 프롬프트 수준), `_new_evidence`/score·verify gate(검증된
전환의 부분 구현)를 갖고 있다. **없는 것**: StateM의 "런북"처럼 **한 런을 넘어 다른 태스크에도
재사용되는 절차적 지식**. 현재 resume/replay(`call_key`)는 같은 런 안에서만 재사용된다.

**검토 후보**: v4.9.0 자동화 모드 S3(calibration.jsonl 통계)를 확장해서, 성공한 producer 라운드의
"계획(plan) 텍스트"나 접근 방식을 유형(bucket)별로 저장했다가 비슷한 신규 태스크의 프롬프트에
참고자료로 주입하는 기능. 단, 원문/민감정보 저장 금지 원칙(S3에서 이미 확립)과 충돌하지 않게
설계해야 함 — "무엇을 했는지"의 요약만 저장하고 원문은 저장하지 않는 방식 검토.

### V-2. Agentic Transaction — 에이전트 작업의 ACID 보장 (arXiv 2608.13900)

**계획서 승격됨(2026-08-24)**: [`docs/plans/v4.x-plan-materialize-transactional-publish-2026-08-24.md`](plans/v4.x-plan-materialize-transactional-publish-2026-08-24.md)
— 검토 결과 **구현 권고**(V-1과 달리 실 데이터·쿼터 승인 불필요, BUG-53이 이미 남긴 잔여 위험을
정확히 메움). `_materialize_outputs()`를 stage-then-publish로 바꾸는 설계 초안 있음 — 진행
여부는 사용자 승인 필요.

**출처 요지**: 에이전트가 여러 도구/API를 건드리다 중간에 실패하면 "부분 실행 후 죽음"이
오염된 상태를 남긴다 — DB 트랜잭션의 원자성·일관성·격리·영속성(ACID) 개념을 에이전트
작업에 적용하자는 제안(Claude Code 대비 10.6%p 향상, KramaBench). 핵심 기법: 탐색-실행-검증
사이클, 재사용 가능한 "트랜잭션 스킬", 신뢰도 편차 기반 검증, 의존성 인식 격리, 트랜잭션
인식 상태 관리.

**우리와의 접점**: 사용자가 직접 지적 — jira-for-me 프로젝트의 휴지통 purge 소유권 토큰·업로드
롤백 이슈와 문제의식이 정확히 겹침. yok3x 자체에도 관련 있는 지점: `materialize`(파일 게시)가
중간에 실패하면 일부 파일만 써진 상태로 남을 수 있음, MCP 워커도구(T-4, a2 미착수)가 여러
외부 도구를 호출하다 실패하면 부분 상태가 남을 수 있음.

**검토 후보**: `materialize` 쓰기를 원자적으로(임시 디렉터리에 전부 쓴 뒤 성공 시에만 최종
위치로 이동) 만드는 게 저비용 고효과 개선일 수 있음 — 실제로 부분 실패 사례가 관측되면
우선순위 올리기. T-4(a2 MCP 중앙 실행기) 설계 시 이 논문의 "트랜잭션 인식 상태 관리" 개념을
참고 후보로 남김.

### V-3. AutoResearch 실패 분류 — 메타인지 루프 부재 (arXiv 2608.14905)

**계획서 승격됨(2026-08-24)**: [`docs/plans/v4.x-plan-metacognitive-loop-review-2026-08-24.md`](plans/v4.x-plan-metacognitive-loop-review-2026-08-24.md)
— 원문 확인 결과 논문 자체가 해결책을 제시 안 함(저자들이 "오케스트레이션 개입으로 해결
가능한지는 이 연구가 테스트 안 한 열린 질문"이라 명시). **신규 구현 권고 없음** — 대신
T-1(심판 신뢰도) 해석에 참고할 진단 렌즈로만 활용: 심판도 모델이라 같은 메타인지 결핍을
가질 수 있음.

**출처 요지**: 실제 연구 과제 100건에서 에이전트 실패를 진단, 45가지 실패 패턴을 분류
(AutoResearch Failure Taxonomy). 핵심 발견: 대부분의 실패가 **메타인지 루프 부재** —
"생산한 결과를 발견한 내용과 비교 검증하고, 안 맞으면 수정하고, 택한 경로가 타당한지
의문을 제기하는 능력"의 부재로 수렴한다.

**우리와의 접점**: yok3x의 producer-reviewer 패턴 자체가 이미 "타인이 검증"하는 구조라 순수
자기검증 부재 문제는 어느 정도 완화돼 있음. 다만 **producer 자신의 자기검증**(require_selfcheck)이
얼마나 실제로 "발견한 내용과 대조"하는지는 별도 검증 필요 — anti_hallucination 설정의
`require_citations`/`flag_unverified`가 관련 있지만 이 논문 관점에서 재평가해볼 가치 있음.

**검토 후보**: 상세 45개 패턴 목록은 이번엔 HF 요약 페이지에서 못 얻었음(원문 arXiv 확인 필요) —
나중에 시간 내서 원문을 읽고 yok3x의 실패 모드(BUG-NN 시리즈)와 대조해보면 새로운 방어 지점을
발견할 수도 있음.

### V-4. herdr — 멀티 에이전트 터미널 멀티플렉서 (외부 도구, https://herdr.dev)

**계획서 승격됨(2026-08-24)**: [`docs/plans/v4.x-plan-herdr-integration-2026-08-24.md`](plans/v4.x-plan-herdr-integration-2026-08-24.md)
— herdr.dev 재확인 결과 핵심 가치는 "멀티플렉서"가 아니라 **재부팅/접속끊김을 넘는 에이전트
세션 지속성**. **코드 통합 권고 안 함**(yok3x 자체 subprocess 실행 계층과 계층이 겹치고
의존성0 원칙과도 안 맞음) — 사용자 개인 도구로 설치해보는 것만 권고.

**출처 요지**: Rust 기반 tmux류 터미널 멀티플렉서. Claude Code/Codex/Amp/OpenCode 등 여러 AI
코딩 에이전트를 워크스페이스/탭/패인으로 띄우고 각각의 상태(working/idle/blocked)를 실시간
표시. CLI + 로컬 소켓 API 제공 — 스크립트로 워크스페이스 생성·패인 분할·에이전트 시작
(`agent start --kind claude|codex`)·프롬프트 전송(`agent prompt`)·상태 대기(`agent wait --until
idle|done|blocked`)·출력 읽기(`pane read`/`pane wait-output`)가 전부 가능.

**우리와의 접점**: 이번 세션에서 codex를 여러 번 백그라운드로 디스패치하고 출력 파일을 폴링해서
확인하는 작업을 계속 했는데, herdr의 socket API가 하는 일(에이전트 시작→상태 대기→출력 읽기)과
개념적으로 거의 같음. yok3x는 이미 자체 backend 서브프로세스 실행 계층(`backends.py`)이 있어서
herdr로 완전히 대체하긴 어렵지만, **사람이 여러 yok3x 워커/코덱스 세션을 한 터미널에서 시각적으로
지켜보는 용도**로는 바로 쓸모 있어 보임(yok3x GUI가 웹 기반인 것과 별개로, 터미널 네이티브
워크플로우를 선호할 때).

**검토 후보**: (a) 그냥 사용자가 도구로 설치해서 써보기(yok3x 코드 변경 불필요), (b)
`backends.py`가 herdr 소켓 API를 선택적 백엔드 실행 경로로 지원하는 것(의존성 0 원칙과
충돌 안 함 — claude/codex처럼 이미 외부 바이너리를 shell out하는 것과 같은 종류). (b)는
실제 이점(신뢰성·가시성 향상 vs 기존 subprocess 방식)이 뭔지 먼저 따져봐야 함 — 지금은
그냥 후보로만 남김.

### V-5. 남은 쿼터 기반 모델·effort·에이전트(backend) 자동 셋팅 (2026-08-24 등록, 사용자 제안)

**계획서 승격 및 §4.1 구현 완료(2026-08-28)**: [`docs/plans/v4.x-plan-quota-aware-model-effort-agent-selection-2026-08-25.md`](plans/v4.x-plan-quota-aware-model-effort-agent-selection-2026-08-25.md)
— candidate backend별 실제 pace→S4 관측 배선과 producer/reviewer별 현재값·보수적 후보 표시를
완료했다. full의 실제 max_rounds/pass_score/effort 및 model/backend 자동 전환은 바꾸지 않았으며,
T-1·quota 계측·모델 비용 메타데이터가 준비될 때까지 계획서 §4.2는 계속 보류한다.

**후속(2026-08-30)**: §4.2 보류 사유였던 T-1(심판 미검증 우려)이 mutation-testing 파일럿으로
사실상 해결됐다(위 T-1 항목 참고 — 4/4 결함 정확 지적, 표본은 작음). §4.2 착수 여부는
여전히 별도 사용자 판단 필요 — 이 문서의 "보류" 결론은 아직 바꾸지 않았다.

**나머지 두 전제조건 실측 점검(2026-08-30)** — 결론: **여전히 미충족, §4.2 보류 유지**.

1. **쿼터 계측 신뢰도** — `yok3x limits --json`으로 실시간 조회한 결과, backend마다 신뢰도가
   완전히 다르다:
   - `codex`: `measured, real=True` — 공식 app-server JSON-RPC 실시간 조회, 알려진 리스크 없음. 자동화 근거로 안전.
   - `claude`: `measured, real=True`지만 `claude_oauth` 경로 사용 중(이 저장소 yok3x.json 설정) —
     [BUG-27](reports/bugs/BUG-27-claude-oauth-usage-probe-blocked-policy.md)에 "정책 보장
     없는 unsupported access"로 명시됨. 과거 한 번 정책 변경으로 429 차단당했다 나중에 철회로
     복구된 이력 있음 — **언제든 measured→unavailable로 조용히 꺼질 수 있다는 전제로 폴백
     설계 필수**.
   - `gemini`: `unavailable, ok=False` — `limits.py`의 `_probe_uncached` 디스패치 테이블에
     `"ledger"`를 처리하는 분기 자체가 없어(코드 확인), `yok3x.json`에 `type: "ledger"`를
     명시해도 "probe 미설정" 폴백으로 떨어진다. **구조적으로 신호 0** — 자동화 근거에서
     아예 빼야 함(신호 없이 자동 전환하면 무작위 결정과 같음).
2. **모델 비용 메타데이터** — `models_catalog`(논리모델명→backend/model_id)와
   `benchmarks`(상황별 품질 점수)는 있지만, **모델별 비용(가격) 필드가 어디에도 없다**
   (코드 확인). 유일한 비용 수치는 `guard.reservation.usd_per_1k_tokens: 0.03` 하나뿐이고
   이건 모델·backend 무관 전역 추정치다. "쿼터를 아낀다"는 목표 함수로 후보를 순위화하려면
   haiku/sonnet/opus/gpt-5.6 등 모델별 실제 단가가 있어야 하는데 **완전히 부재** — 새로
   소싱·설계해야 함.

**결론**: T-1은 해결됐지만 나머지 두 전제조건은 아직 채워지지 않았다. 실제 착수하려면
(a) codex 중심으로 스코프를 좁히고 claude는 폴백 설계·gemini는 자동화 대상에서 제외,
(b) 모델별 실제 단가를 `models_catalog`에 추가하는 선행 작업이 필요 — 둘 다 사용자 승인
후 별도로 진행.

**출처**: 사용자 제안 — "남은 쿼터에 따라서 자동으로 모델이랑 모델의 effort 수준이랑 사용하는
에이전트까지 자동으로 셋팅해보는 기능".

**이미 있는 것(v4.9.0 자동화 모드)**: `automation.py`의 S2(`recommend_effort_rounds`)가
작업 특징(bucket)에 따라 effort/rounds를 추천하며, full+`allow_effort_adjustment=True`에서는
이 **S2 정적 추천**을 producer/reviewer에 적용한다. S4(`plan_quota_aware_effort_rounds`)도
`daily_pace` 스냅샷(warn/stop 등급)을 받으면 rounds→effort 순으로 낮추는 순수 함수와 테스트는
있다. 이제 실제 `run_task_file()`은 candidate backend별 pace snapshot을 넘겨 S4 후보를
`status.json`에 관측값으로 남긴다. 단 `recommendation`은 기존 S2 실행 기준으로 유지하므로,
남은 quota 기반 후보는 assist 표시일 뿐 실제 실행값에는 적용되지 않는다.

**없는 것(진짜 새로운 부분)**: `resolve_model()`/`backend_available()`은 "이 backend가 설치돼 있고
한도가 stop이 아닌가"를 보는 boolean 필터이며 warn에는 순위 페널티가 없다. 별도의 기존
`degrade_plan()`(기본 90% 이상에서 opt-in lite 모델)과 `failover_backend()`(기본 97%/stop에서
opt-in backend 전환)는 있어 **선제 로직이 전혀 없는 것은 아니지만**, reset까지 남은 시간과
지속 가능 pace를 보고 더 일찍 모델/backend 후보를 재순위하는 계획 단계 로직은 없다. 또한
producer/reviewer 같은 worker 역할 자체를 quota로 자동 선택하는 기능도 없다.

**검토 후보(구현 전, 설계만)**:
1. `plan_quota_aware_effort_rounds`의 warn/stop 판정을 backend *선택* 자체에도 확장 —
   지금은 "이 backend를 쓸 수 있나(available)"만 boolean으로 보는데, "이 backend를 지금
   쓰는 게 페이싱상 안전한가"까지 반영해 `resolve_model()`의 candidate 순서에 페널티를 주는 방식.
2. "모델"(같은 backend 안에서 더 싸거나/빠른 모델로 다운그레이드)은 지금 `models_catalog`/
   `benchmarks` 구조로 후보를 낼 수 있고 고정 lite 열화도 있지만, "쿼터를 아낀다"는 목표 함수로
   전체 후보를 순위화할 비용 메타데이터·로직은 없음 — 이건 새 설계가 필요.
3. **주의**: T-1/T-2가 아직 "심판(codex-critic)이 SCORE를 신뢰할 만큼 잘 매기는지" 자체를
   검증 못 한 상태(캘리브레이션 데이터 부족)라, 모델/effort를 자동으로 낮추는 기능을 먼저
   만들면 "저품질 산출물이 저품질 심판을 통과"하는 조합이 생길 위험이 있음 — v5.0.0
   계획서(나이틀리 자가개선)의 위험 A("심판 미검증")와 같은 종류의 우려. 실제 구현 전
   T-1 완료 여부를 먼저 확인하는 게 안전.

다른 비전 항목과 동일 — 여기 있다는 것 자체가 "하기로 결정"을 뜻하지 않음. 나중에 훑어보고
가치가 있으면 별도 계획서로 승격.

### V-6. 설치한 사람들끼리 작업 공유 기능 (2026-08-27 등록, 사용자 제안)

**계획서 승격됨(2026-08-30)**: [`docs/plans/v4.x-plan-teamflow-work-sharing-2026-08-30.md`](plans/v4.x-plan-teamflow-work-sharing-2026-08-30.md)
— TeamFlow(jira-for-me) 기존 MCP 서버 연동, yok3x 코드 변경 없이 설정+프롬프트 지시로 구현.
**실행 보류(2026-08-30, 사용자 결정)** — 계획서까지만 완료해두고 실행(스모크 테스트:
`yok3x-bot` TeamFlow 계정 생성 → 실 작업 1건 시험)은 나중에 사용자가 다시 꺼낼 때 진행.
계획서의 "결정 필요" 2개(스모크 테스트 착수 여부·reviewer rubric에 규칙위반 점검 추가
여부) 둘 다 미결정 상태로 대기.

**출처**: 사용자 제안 — "이거 설치한 사람들끼리 서로 작업 공유하는 기능 만들고 싶어."

**설계 질문 답변 완료(2026-08-30) — 방향이 크게 바뀜**: 처음부터 새로 만들지 않고, 사용자가
이미 갖고 있는 **jira-for-me(TeamFlow)**를 공유 레이어로 쓰는 쪽으로 결정됐다
(`C:\Users\playdata2\Documents\test_workspace\jira_for_me`, 배포: jira-for-me.vercel.app).
확인해보니 이게 실제로 아주 잘 맞는다 — 그리고 T-4에서 "실사용 0건"이라 보류했던 yok3x의
a1(MCP 워커도구) 기능이 마침내 **첫 실사용 후보**를 얻는다.

- **무엇을**: task/이슈 정의 + 상태·진행상황(스프린트) — 원문 코드/프롬프트가 아니라
  **작업 메타데이터**만 오간다. 프라이버시 우려가 오히려 구조적으로 완화된다 — TeamFlow의
  MCP 서버엔 애초에 **댓글 작성 도구가 없다**(세션 전용, 토큰 인증으로는 401 — "알려진
  한계"에 명시) — 즉 지금 구조로는 실수로 생성된 코드/프롬프트 전문을 TeamFlow에 흘려보낼
  경로 자체가 없다.
- **어떻게**: `jira_for_me/mcp-server/`에 이미 완성된 `teamflow-mcp-server`(stdio MCP,
  API 토큰 인증, `list_projects`·`list_issues`·`get_issue`·`create_issue`·
  `update_issue_fields`·`create_sprint`·`manage_sprint` 등 제공)를 그대로 쓴다. yok3x
  쪽은 **새 코드 불필요** — `cfg.yok3x["mcp_servers"]`에 `teamflow` 서버 spec을 등록하고,
  워커가 `mcp_tools.servers: ["teamflow"]`로 opt-in하면 끝(a1 설계 그대로).
- **누구와**: `jira_for_me/docs/briefs/ai-agent-integration-scaling.md`(그 프로젝트 자체의
  브리핑 문서)가 이미 이 질문에 답을 정해준다 — TeamFlow는 지금 **한 팀 전용 설계**이고,
  "여러 독립된 팀/조직이 쓰게 되면"(호스티드 MCP·멀티테넌시·OAuth)은 그 프로젝트가 명시적으로
  "지금 안 함, 트리거 발생 시 재검토"로 미뤄뒀다. 따라서 V-6의 실제 범위는 **"yok3x
  설치자 누구나"가 아니라 "이미 같은 TeamFlow 프로젝트의 멤버인 협업자끼리"**로 자연스럽게
  좁혀진다 — 더 넓히려면 yok3x가 아니라 jira-for-me 쪽에서 별도의 큰 작업(호스티드 MCP)이
  선행돼야 하며, 그건 이 계획서의 범위 밖이다.

**남은 설계 질문 답변 완료(2026-08-30, 사용자 제안 + codex 논의)**: 사용자가 "task↔issue는
1:1로, 라운드는 desc에 누적"을 제안 → 실제 API 제약(코드로 확인: `update_issue_fields`는
"본인이 등록한 이슈만" 수정 가능, `desc`는 z.string() 통째 교체만 되고 append 연산 없음,
Postgres `text`라 DB 길이제한은 없지만 API 레벨 검증도 없음)을 근거로 codex와 비판적으로
검토. **결론: 세 가지 우려(소유권·동시성 경쟁·desc 오염) 전부 타당**, desc 누적은
프로토타입 이상으로 권하기 어려움 — 아래처럼 수정해 확정:

1. **task↔issue 매핑**: 1:1 유지. 단 "사람이 원래 갖고 있던 이슈"와 "yok3x가 다루는
   실행 이슈"를 구분한다 — yok3x 전용 계정(`yok3x-bot`)이 자동화 실행 이슈를 전담
   생성·소유하고, 사람이 미리 만든 이슈는 직접 고치지 않고 그 실행 이슈가 참조만 한다
   (사람 이슈 TF-123 ← 참조 ← yok3x 실행 이슈 TF-456). 소유권 제약(creator만 수정 가능)을
   API 변경 없이 우회하는 가장 현실적인 방법.
2. **댓글 도구 부재 대응**: desc를 "라운드 누적 로그"가 아니라 **"최신 체크포인트
   스냅샷"**으로 쓴다 — 매 라운드마다 이어붙이지 않고 통째로 덮어씀(예: "reviewer 수정요청
   반영 중 · 테스트 11/12 통과 · 다음: 만료토큰 테스트 수정"). **전체 라운드 이력은
   yok3x 로컬(`.yok3x/runs/`)에만 남기고, TeamFlow엔 지금 상태+최종 결과만 반영**한다.
3. **라운드↔서브태스크 계층**: 안 함. 라운드마다 별도 이슈를 만들면 이슈 수가 폭증하고
   1:1 원칙이 깨진다(codex도 비권장) — 1:1을 유지하고 라운드 상세는 로컬 로그에만.
4. **동시성**: 같은 이슈는 항상 하나의 orchestrator만 쓰도록 직렬화(봇 계정 공유만으로는
   race가 안 풀림 — codex 지적).
5. **장기 wishlist**(yok3x가 아니라 jira-for-me 쪽 별도 작업): append-only 활동 로그 API,
   creator 외 automation 역할에게 위임 가능한 수정 권한이 생기면 desc 스냅샷 우회 자체가
   불필요해짐 — 지금은 그 API가 없어서 우회.

**검토 후보**: 설계 질문이 사실상 다 확정됐으니 계획서로 승격 준비 완료 — a1(MCP)의
실사용 검증도 자연스럽게 같이 된다(T-4 참고, 지금까지 실사용 0건이었음).

### V-7. FreeToken류 로컬 MoE 서빙 엔진을 offline degrade 백엔드로 연결 (2026-08-30 등록, 사용자 제안)

**출처**: 사용자가 공유한 논문 — [FreeToken: Efficient Edge-Native MoE Serving with
Bandwidth-Adaptive Execution](https://arxiv.org/abs/2608.16157)(2026-08-17, Song Han·Matei
Zaharia·Ion Stoica 등 공저). GPU VRAM만이 아니라 GPU+CPU+RAM+PCIe 대역폭 전체를 동적
자원 풀로 취급해, 소비자급 하드웨어(8GB 노트북 GPU 등)에서도 수백B급 MoE를 실사용 가능한
속도로 서빙한다고 보고. 논문 자체는 미검증(프리프린트, 벤치마크 수치는 사용자 전달 그대로).

**우리와의 접점**: yok3x는 추론 엔진이 아니라 claude/codex/gemini CLI를 shell-out하는
오케스트레이터라서(의존성0 원칙), FreeToken의 실제 기법(expert caching·PCIe-aware
placement·prefill/decode 분리)을 코드에 이식하는 건 애초에 범위 밖이다. 대신 이미 있는
연결점 하나: `backends.json`의 `local` 백엔드(범용 OpenAI-호환 HTTP, `base_url:
http://localhost:8000/v1`)와 `cfg.yok3x["guard"]["degrade"]["offline_backend"]="local"`
(클라우드 쿼터 전멸 시 로컬로 강등하는 기존 opt-in 폴백 경로, `tests/test_yok3x.py`에
단위 테스트 있음 — a1처럼 완전히 미사용은 아님). FreeToken이 OpenAI-호환 서버 모드를
제공한다면, `backends.json`의 `local.base_url`/`model`만 그쪽으로 바꾸면 **코드 변경
전혀 없이** 이 폴백이 약한 로컬 모델 대신 실사용 가능한 대형 MoE로 바뀐다.

**검토 후보**: V-4(herdr)와 같은 결론 — **코드 통합 대상이 아니라 개인 인프라 도구로
설치해 `local` 백엔드 설정만 바꿔 끼우는 방식**을 권고. 실제 로컬 서버를 띄워 강등
경로를 진짜로 타보는 실사용 검증은 아직 없음 — 필요해지면(클라우드 쿼터를 자주 전멸시킨다면)
그때 시도해볼 후보.

### V-8. 어려운 변경은 "micro-world"(상호작용 게임)로 이해시키기 — Cognitive Sync Layer 확장 (2026-08-30 등록, 사용자 제안)

**검토 완료(2026-08-30)** —
[`docs/plans/v4.x-plan-micro-worlds-cognitive-sync-2026-08-30.md`](plans/v4.x-plan-micro-worlds-cognitive-sync-2026-08-30.md):
micro-worlds가 얹힐 기반인 Cognitive Sync Layer(S6a/S6b/S7)가 **실사용 0건**임을 확인
(`understanding_bundle.json`이 파일시스템 어디에도 없음 — `yok3x sync`가 실제로 산출물을
만든 적이 한 번도 없음). T-4(MCP a2) 검토와 같은 종류의 순서 오류 — 기반이 검증 안 된
상태에서 더 비싼 확장부터 만들 근거가 없음. **구현 보류 권고**, 재검토 조건은 문서 참고.

**출처**: 사용자와 이전에 시청·논의한 [Geoffrey Litt(Notion), "Understanding is the new
bottleneck"](https://youtu.be/WkBPX-oDMnA) 발표. 핵심 주장: 에이전트가 코드를 점점 더
빨리 생산하면서 병목이 "정확성"에서 "사람이 그걸 이해하는가"로 옮겨갔다. 발표가 제안한
3가지 기법 중 우리와 가장 직접 관련된 것 — **micro-worlds**: 원본 코드를 그냥 읽게 하는
대신, 에이전트가 **별도의 작은 상호작용 프로그램(게임)**을 만들어 "타임라인을 드래그하며
관찰"하거나 "단계별 사이드바이사이드 비교" 같은 조작·관찰로 이해시키는 방식(예시: Prolog
디버거, Astro 마이그레이션 게임). 발표의 다른 두 기법("ExplainDiff" 설명문서+퀴즈,
"shared spaces" 협업 공간)은 각각 아래 "우리와의 접점"·V-6과 이미 겹친다.

**우리와의 접점**: yok3x는 이미 [Cognitive Sync Layer](plans/v4.6.0-plan-cognitive-sync-layer-2026-08-08.md)(`sync_layer.py`)로
발표의 "ExplainDiff" 트랙(설명 문서 + comprehension quiz, S6a 온디맨드 클릭형·S6b 표준
자동생성)을 이미 구현해뒀다 — 다만 **micro-worlds 트랙(상호작용 게임)은 없다.** 지금
sync_layer는 정적 텍스트(설명·퀴즈 질문)만 만들고, "직접 조작해보며 이해"하는 산출물은
안 만든다. 사용자가 말한 "어려운 건 게임 만들어주는 기능"이 정확히 이 빈틈이다. 참고로
`sync_layer` 자체가 기본 off(opt-in)라, 퀴즈 트랙부터도 아직 실사용 검증이 없는 상태다.

**검토 후보(구현 전, 설계만)**:
1. 대상 선별: 모든 변경에 micro-world를 만들면 비용·복잡도가 크다 — S6b의 comprehension
   quiz에서 "이해도 낮음"으로 판정된 claim이나, 사용자가 명시적으로 어렵다고 표시한
   부분에만 온디맨드로 생성하는 방식이 합리적(S6a의 "클릭해서 요청" 패턴과 같은 원칙).
2. 무엇을 만드는가: 코드 자체를 실행하는 게 아니라, **그 코드가 하는 일을 시뮬레이션하는
   훨씬 작고 단순화된 별도 프로그램**을 에이전트가 생성 — 원본 코드베이스에 영향 없이
   독립 산출물(HTML/JS 등)로 격리해야 안전(yok3x의 "텍스트 생산자=안전" 설계와 충돌 최소화).
3. 비용: micro-world 생성 자체가 추가 LLM 호출 1회 이상 — sync_layer의 기존 비용 단계
   (light/standard/deep) 구조에 자연스럽게 끼워 넣을 수 있음(예: deep 모드 전용).
4. T-1 mutation-testing 파일럿처럼, 이것도 "만들어놓고 실제로 이해에 도움 되는지"를
   검증할 방법이 필요함 — 예: 사용자가 micro-world를 본 뒤 comprehension quiz 정답률이
   실제로 오르는지 비교(sync_layer의 기존 calibration 연동, S7과 접점).

다른 비전 항목과 동일 — 여기 있다는 것 자체가 "하기로 결정"을 뜻하지 않음. 계획서로
승격할 때 sync_layer 기존 구조와의 정합성부터 확인.

### V-9. 실측 결과 기반 에이전트 종류·수·라운드 자동 결정 (2026-08-30 등록, 사용자 제안) — **축소판 구현 완료**

**출처**: 사용자 제안 — triage/automation의 pattern·rounds 추천이 텍스트 길이 같은 정적
특징뿐이라, "실제 작업 진행 결과를 바탕으로" 결정하는 게 "근거있는" 방식 아니냐는 지적.

**계획서**: [`docs/plans/v4.x-plan-rounds-calibration-hint-2026-08-30.md`](plans/v4.x-plan-rounds-calibration-hint-2026-08-30.md)

**조사 결과 — 원래 제안(에이전트 종류·수)은 아직 불가능**: 실 calibration 기록 24건(메인
저장소 2+T-2 22)이 **전부 producer-reviewer 패턴**이라 solo 등 다른 패턴과 비교할 대조군이
없다. V-1이 부딪혔던 것과 같은 선행조건 미충족(이번엔 표본 수가 아니라 변수 다양성 0).

**축소판(라운드 수)은 구현 완료**: `calibration.py`가 이미 `bucket` 필드를 기대하도록
설계돼 있었는데 orchestrator가 한 번도 채운 적이 없었다는 걸 발견 — 이제 매 런마다
자동으로 채우고(비용 0, 상시), `rounds_by_bucket`/`rounds_hint_for`로 bucket+pattern별
실측 라운드 중앙값·성공률을 집계한다. **로그로만 노출**(`automation.show_rounds_calibration_hint`,
기본 off) — `max_rounds`를 자동으로 바꾸진 않는다(V-5§4.1과 같은 원칙, 표본 편향 강화
위험 때문). 테스트 10개 추가, 전체 스위트 688 passed.

**재검토 조건(원래 제안)**: solo 등 다른 패턴의 실 실행 기록이 쌓이면, 같은 방식으로
pattern별 success_rate/cost 비교 함수를 추가하는 게 자연스러운 다음 단계.

### V-10. knot(지식그물)에 OKF(Open Knowledge Format) `type` 필드 도입 — **구현 완료** (2026-08-30 등록, 사용자 제안)

**출처**: 사용자 질문 — "오픈위키나 OKF 같은 거 도입 안해도 됨?" 웹 리서치(2026-08-30
기준)로 확인: "오픈위키"는 Andrej Karpathy의 LLM Wiki 개념, **OKF**는 Google Cloud가
2026-06-12 공개한 그 개념의 표준화 결과(현재 v0.2, [스펙](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)) —
벡터DB·임베딩 없이 마크다운+YAML frontmatter+명시적 링크로 지식을 구조화, git 버전관리.

**계획서 및 구현 완료**: [`docs/plans/v4.x-plan-okf-knowledge-format-2026-08-30.md`](plans/v4.x-plan-okf-knowledge-format-2026-08-30.md)
— `knot.py`가 이미 OKF 취지("평문 마크다운으로 에이전트끼리 기억 공유")를 80% 구현하고
있었음을 확인, OKF의 유일한 필수 필드 `type`만 추가(Phase 1, 공식 가이드 기준 최소
마이그레이션). `knot.save()`에 `type` 매개변수(기본 `"Note"`, 기존 호출부 안 깨짐),
`cli.py`에 `--type` 옵션, orchestrator 자동 노트에 `Run Stall`/`Run Summary` 부여,
`lint()`에 누락 검사 추가(관측만, 강제 안 함 — 기존 원칙). 기존 23개 노트·링크 문법(실사용
0건이라 리스크 없음 확인)은 그대로 둠. 테스트 3개 추가, 전체 스위트 691 passed·1 skipped
(회귀 없음).

### V-11. 계정 스위칭 — 같은 backend의 다른 계정으로 쿼터 전환 — **구현 완료** (2026-09-06 등록, 사용자 제안)

**출처**: 사용자 제안 — "계정 스위칭 기능 있으면 좋겠다."

**구현 완료(2026-09-06)**:
1. `backends.py` — backend별 `env`를 자식 프로세스에 주입(`os.environ` 덮어쓰기, `~` 확장,
   미지정 시 `env=None`으로 기존 동작 불변). 이걸로 `claude-alt` 같은 **가상 backend**를
   `CLAUDE_CONFIG_DIR`/`CODEX_HOME`만 다르게 걸어 등록하면 계정이 분리된다.
2. `usage.failover_backend()` — `current_ratio` 인자 추가. 주면 **지금보다 `failover_min_gain`
   (기본 10%p) 이상 여유로운 후보만** 반환하고, 로컬 강등(P3)까지 내려가지 않는다.
   기존 호출(인자 없음)은 종전 동작 그대로.
3. `orchestrator.py` — **폴백 사다리 순서 변경**(사용자 지시: "백엔드 있으면 그거 전환 먼저,
   둘 다 쿼터 없으면 그때 강등"). 종전엔 폴오버 97%·강등 90%라 90~97% 구간에서 여유 있는
   계정을 놔두고 모델만 깎였다 → 이제 강등 임계에서 먼저 전환을 시도한다.
4. `config.py` — `guard.degrade.switch_before_degrade`(기본 True)·`failover_min_gain`(0.1) 추가.

테스트 5개 추가(env 주입·선제전환 최소이득·P3 미강등·전환 우선·대안 없을 때 강등 회귀),
전체 스위트 695 passed·1 skipped(실패 1건은 `test_parallel.py`의 기존 타이밍 플레이크 —
격리 실행 시 18개 전부 통과 확인, 이번 변경과 무관).

**자동화(2026-09-06 사용자 지시 "이런 거는 자동으로 되게 해놔야지")**: `failover_enabled`는
원래 "다른 모델로 넘기면 품질이 달라진다"는 이유로 기본 off였는데, 같은 모델의 다른 계정은
그 근거가 적용되지 않는다. `backends.json`에 `account_of: "claude"`로 계정군을 선언하면 그
후보는 **opt-in 없이 항상** 폴오버 대상이며, 더 한가한 다른 모델보다 우선 선택된다. 다른
모델로의 폴오버만 종전대로 `failover_enabled` opt-in.

**codex 쿼터 추적 계정 분리 — 구현 완료(2026-09-06)**: `switch_before_degrade`가 "지금보다
`failover_min_gain` 이상 여유로운 계정"을 판단하려면 그 계정의 **실측 쿼터**가 필요한데,
`limits._probe_codex_appserver`(라이브 app-server RPC)는 `env` 주입 없이 항상 기본
`CODEX_HOME`만 조회해 두 번째 계정을 볼 방법이 없었다(전환 로직 자체는 되는데 판단에 쓸
실측치가 없는 상태 — 세션 파일 폴백만 `sessions_dir`로 이미 계정 분리가 됐던 것과 비대칭).
`limits.<alt>.sessions_dir`가 있으면 그 부모 디렉터리를 `CODEX_HOME`으로 삼아 app-server를
띄우도록 고쳤다(`_appserver_rate_limits`에 `env` 매개변수 추가) — 기존에 쓰던 것과 같은
설정 키 재사용이라 새 필드 없음. 미지정 시 `env=None`으로 기존 동작과 바이트 단위로 동일.
테스트 2개 추가(alt 계정 env 주입 확인·미지정 시 회귀 없음), 전체 스위트 699 passed·1 skipped
(기존 `test_parallel.py` 플레이크, 이번 변경과 무관).

**남은 것**:
1. 실제 두 번째 계정으로 end-to-end 검증(사용자 계정 준비 필요) — `backends.json`에
   `account_of: "codex"` + `env.CODEX_HOME`로 가상 backend 등록, `limits.<alt>.sessions_dir`를
   같은 CODEX_HOME의 `sessions` 하위로 지정하면 실행·쿼터추적 둘 다 분리된다(위 항목으로 준비는
   끝남 — 남은 건 실제 두 번째 계정 준비뿐).
2. **설정 화면 UI**(사용자 지시) — `switch_before_degrade`·`failover_min_gain`·계정군 표시를
   GUI에서 조정. RULE상 UI는 codex가 구현하고 Claude가 검토하는데, 2026-09-06 기준 codex가
   쿼터 소진(ratio 1.0, stop)이라 착수 못 함. codex 복구 후 진행.
3. gemini 계정 분리는 **아직 안전한 방법이 없다**(위 표 참고 — `~/.gemini` 하드코딩). API 키
   방식이면 `GEMINI_API_KEY`로 되지만 OAuth 계정은 홈 디렉터리를 통째로 바꾸는 우회뿐이라
   부작용이 크다. 실제로 gemini 계정을 둘 이상 쓸 일이 생기면 그때 다시 판단.

**실현 가능성 조사 완료(2026-09-06)** — 3개 backend 모두 환경변수로 계정 격리가 가능하다:

| backend | 격리 수단 | 확인 방법 |
|---|---|---|
| claude | `CLAUDE_CONFIG_DIR` | `claude.exe` 바이너리에 해당 문자열 존재 확인 |
| codex | `CODEX_HOME` | `codex --help`의 `--profile` 설명이 `$CODEX_HOME/<name>.config.toml` 참조 |
| gemini | API 키(`GEMINI_API_KEY`) 또는 `USERPROFILE`/`HOME` | **전용 config 변수 없음** — CLI 번들에 `GEMINI_DIR = ".gemini"`가 **하드코딩**돼 있고 경로가 `path.join(homedir(), GEMINI_DIR)`라 `~/.gemini` 고정. OAuth 계정을 분리하려면 홈 디렉터리 자체를 바꾸는 우회뿐(부작용 있음) |

**핵심 발견 — "가상 백엔드"로 등록하면 기존 기계장치가 그대로 작동한다**: `usage.py:888`의
`failover_backend`가 `for b in (cfg.backends or {})`로 **모든 backend를 일반적으로 순회**하고,
`limits.py:1321`의 `_claude_root(conf)`도 `conf.get("projects_dir")`로 **backend별 경로 지정이
가능**하다. 두 번째 계정을 `backends.json`에 `claude-alt` 같은 별도 backend로 등록하면 쿼터
추적·원장·페이싱이 **자동으로 계정별 분리**되고, failover가 사용률 최소 backend를 고르므로
여유 있는 계정을 자동 선택한다.

**필요한 코드 변경은 하나**: `backends.py:215`의 `subprocess.run(...)`이 `env=`를 안 넘긴다 —
backends.json에 per-backend `env` 필드를 지원하도록 추가해야 한다(약 10줄).

**설계 판단 필요(구현 전)**: 지금 폴백 사다리는 모델 강등(90%) → backend 폴오버(97%) 순인데,
계정 스위칭은 **모델 품질을 유지한 채 쿼터만 새로 얻으므로 강등보다 먼저 와야 논리적**이다.
지금 구조로는 "claude 90% → haiku 강등"이 먼저 터지고 멀쩡한 두 번째 계정이 놀게 된다 —
사다리 순서 조정은 기존 `degrade_plan()` 로직을 건드리는 별도 판단이 필요.

### V-12. OpenClaw/Hermes 등 외부 개인 에이전트 연동 안전장치 — **구현 완료** (2026-09-06 등록, 사용자 제안) · YOK-99

**출처**: 사용자 제안 — OpenClaw·Hermes 같은 상시구동 개인 에이전트에서 yok3x를 편하게
쓰도록 최적화하고 싶다. 웹 리서치(2026-09-06 기준)로 확인: 둘 다 **상시 구동 + 메시징/
cron/webhook 같은 외부 이벤트로 트리거**되는 게 핵심 특성(OpenClaw 38만+ GitHub 스타;
Hermes는 "smart approvals 기본 켜짐") — yok3x가 전제해온 "사람이 세션에서 직접 명령"과
질적으로 다르다.

**"기존 방어 장치로 충분한가"에 대한 답 — 코드로 확인한 결과 아니었음**: `--auto`(CLI
호출 단위)와 `guard.reservation.max_usd_per_run`(런당 비용 상한, 기본 0=무제한)은 이미
있었지만 **서로 묶여있지 않았다** — `--auto` 쓰면서 상한을 0으로 둬도 막는 코드가 없었다.
"무인 호출 + 무제한 지출"이라는 위험한 조합이 그대로 가능했다.

**구현 완료**: [`docs/plans/v4.x-plan-agent-integration-safety-2026-09-06.md`](plans/v4.x-plan-agent-integration-safety-2026-09-06.md)
— 신규 `--unattended` 플래그(기존 `--auto`는 그대로 두고 opt-in 추가): `--auto`를 자동
내포하고, 비용 상한 미설정 시 워커 호출 전에 `cause=unattended_requires_cost_cap`로
fail-closed 거부. `status.json`에 `unattended: true` 기록(감사 가능). 그리고
`.claude/skills/yok3x-usage/SKILL.md` 작성(Agent Skills 형식 — 지난주 DisCo 논문에서 확인한
사양과 동일, Claude Code뿐 아니라 이 사양을 지원하는 다른 에이전트도 스스로 발견해 읽을 수
있음) — yok3x 사용법·task.json 스키마·"하지 말아야 할 것"(전역 `auto_approve=true` 금지,
`--unattended` 대신 `--auto`만 쓰지 말 것, workdir·자격증명 분리)을 문서화. 테스트 3개
추가, 전체 스위트 702 passed·1 skipped(회귀 없음).

**범위 밖(다음에 필요해지면)**: yok3x 자체를 MCP 서버로 감싸는 것(teamflow-mcp-server
패턴 재사용) — 실사용 수요 확인되면 재검토. `verify_cmd`·`strict` 게이트를 무인 호출에
강제하는 것도 이번엔 안 함(재정적 위험만 막음, SKILL.md에 강력 권장으로만 남김).

### 참고 문서 — P2P 도입 여부 및 "왜 yok3x인가" 평가 (2026-08-30)

사용자 질문 2건에 대한 조사·결론 — [`docs/reports/v4.x-assessment-p2p-and-project-rationale-2026-08-30.md`](reports/v4.x-assessment-p2p-and-project-rationale-2026-08-30.md).

- **P2P 네트워크**: 지금은 도입 안 함. 2026년 P2P 멀티에이전트 코디네이션 연구(Matrix·
  AgentNet·MOD-X·Google A2A)가 활발하지만, 그 분야 자체가 "증거 기반 신뢰" 문제를 아직
  못 풀었다고 인정함 — 이건 yok3x가 T-1에서 단일 로컬 환경으로도 겨우 검증한 문제라, 낯선
  참가자·네트워크 규모로 확장하는 건 시기상조. 실제 요구("아는 사람끼리 공유")는 V-6로
  충분. 재검토 조건은 문서 참고.
- **"skill로 codex·claude 직접 연동하면 되는데 왜 yok3x냐"**: 일리 있는 비판이라고 인정 —
  `skill-codex`/`codex-delegator` 같은 실제 도구가 있고, "한 번 위임"이 목적이면 그쪽이
  낫다. 다만 yok3x는 그 위임 자체가 아니라 **그 주변의 안전장치**(객관적 verify_cmd 게이트,
  T-1로 실측 검증한 심판 신뢰도, 쿼터 인식 페이싱, 원자적 게시, 캘리브레이션 데이터 축적)를
  다룬다 — 검색한 어떤 대안 도구도 이걸 하지 않음(비교표는 문서 참고). 이 차별점이 실사용
  가치로 이어지는지는 계속 지켜봐야 한다는 점도 정직하게 남겨둠.

---

<!-- AUTO:todo_check START (scripts/todo_check.py가 자동 갱신) -->
### 자동 점검 상태 · 2026-08-10T09:46:47+09:00

⏳ **대기 — 데이터 부족.** 독립런 0/10 · 통과 0/3 · 실패 0/3

- calibration.jsonl: 없음(실런 0) · 전체 0행
- 독립 런(run_id): **0** (기준 ≥10)
- verify 통과 0 · 실패 0 (각 기준 ≥3)
<!-- AUTO:todo_check END -->
