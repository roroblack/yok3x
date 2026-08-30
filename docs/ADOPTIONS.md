# ADOPTIONS.md — 외부 아이디어 채택 원장

yok3x 원칙: **프레임워크는 안 들이고, 외부 연구·도구의 좋은 아이디어만 의존성 0(표준 라이브러리)으로
이식**한다. 무엇을 · 어디서 · 왜 · 어디에 적용했는지를 이 파일에 기록한다. 새 아이디어를 가져오면 여기
한 줄 이상 추가한다(RULE §5.5·§7과 함께). 상세 판단은 각 `reports/*-assessment-*.md`에 있다.

## 채택 완료 (구현됨)

| 출처 | 가져온 아이디어 | 왜 (필요) | 어디에 적용 | 근거 리포트 |
|---|---|---|---|---|
| **Karpathy 4원칙**(에이전트 폭주 방지 운영원칙) | ①작게 나눠 실행 ②사람 승인 ③자가검증 ④예산 상한 | LLM 에이전트가 폭주/무한루프/과금하는 것을 코드 수준 브레이크로 막기 위해 | `orchestrator.py`(단계분할·승인 게이트·SELF-CHECK·요금 가드) | — |
| **ARIS**(적대적 멀티에이전트 검수) | 리뷰어를 '채점'이 아니라 **반증/파괴** 우선으로 + **교차 패밀리** 강제(AD1) | 같은 모델끼리 서로 봐주는 검수의 허점을 없애 결함 검출률을 높이려고 | `orchestrator.py`(ADVERSARIAL_REVIEW·`_ensure_cross_family`), `config.adversarial_review` | `reports/v3.5.0-assessment-aris-adversarial-mode-2026-07-11.md` |
| **LightRAG**(이중레벨 검색) | knot 검색을 **저수준(키워드)+고수준([[링크]]·태그 확장)** 이중레벨로 | 지식그물 검색이 단순 키워드만이면 관련 노트를 놓쳐서 | `knot.query`(dual-level, 의존성0) | `reports/v3.5.0-assessment-lightrag-2026-07-11.md` |
| **Mem0**(프로덕션 장기기억) | **consolidation** 3종 — 요점 추출·중복 통합(lint)·최신성 감쇠(검색 가중) | 런 산출물을 통째로 쌓으면 knot이 비대·중복해져서. 벡터DB 없이 실전 이득만 | `knot.extract_key_points`·`lint`(dedup)·`_recency_weight` | `reports/v3.5.0-assessment-mem0-2026-07-13.md`, `reports/v3.5.0-knot-consolidation-2026-07-13-1600.md` |
| **LangGraph**(조건부 엣지) | **조건부 라우팅(에스컬레이션)** — 낮은 점수 지속 시 워커 1회 전환 | 정적 패턴 한계. 수렴 실패 시 다른/강한 워커로 동적 라우팅(스톨감지·열화의 일반화) | `orchestrator.run_producer_reviewer`(escalate), task spec `escalate` | `plans/v4.0.0-plan-conditional-routing-2026-07-15.md` |
| **LLM-judge 채점 분산 완화 연구**(Rating Roulette EMNLP 2025, G-Eval) | LLM은 결함 탐지(findings)만, 점수(SCORE)는 프로그램이 결정론적 공식으로 계산 — 판정과 채점 책임 분리 | 완전히 동일한 결함 목록에도 SCORE가 stdev 1.6까지 흔들림을 실측(T-1) — "같은 결함, 다른 점수" 분산 제거 | `review_protocol.compute_deterministic_score()`, `cfg.yok3x["review_protocol"]`(opt-in) | `reports/v4.x-result-t1-pilot-spec-test-separation-2026-08-30.md`, `plans/v4.x-plan-deterministic-review-scoring-2026-08-30.md` |
| **Mutation testing**(SWE-ABS ICML 2026, SWE-Mutation ACL 2026 Findings) | 정상 코드에 witness로 검증된 결함을 인위적으로 주입해 심판(reviewer)의 결함 탐지력을 통제 실험으로 직접 측정 | producer가 자연스럽게 실패하는 사례(verify_ok=False)가 구조적으로 안 나와 T-1 착수 조건을 못 채움 — organic 실패를 기다리는 대신 synthetic 결함으로 대체 | T-1 캘리브레이션 절차(코드 통합 없음 — 조사 방법론으로 채택, `yok3x_t1_mutation/` 재현 자료) | `reports/v4.x-result-t1-mutation-testing-pilot-2026-08-30.md` |

## 평가 후 미채택 (프레임워크 통째 도입 배제)

| 출처 | 왜 통째로는 안 가져왔나 | 대신 |
|---|---|---|
| **Mem0 프레임워크**(벡터DB+LLM 추출) | 의존성 0 위배·과설계(주 용도가 장기 다세션 대화가 아님) | 위의 consolidation 패턴만 이식 |
| **LightRAG 프레임워크** | 벡터/그래프 저장 의존성 | 이중레벨 검색 아이디어만 이식 |
| **LangChain/LangGraph 프레임워크** | 의존성·통제/투명성 상실 | 아래 '이식 후보'의 아이디어만 |
| **Zed ACP**(에이전트 클라이언트 프로토콜) | 통합 이점 대비 범위 밖 | 보류 — `plans/v3.4.0-plan-zed-acp-integration-2026-07-11.md` |

## 이식 후보 (분석 완료, 계획/구현 대기)

| 출처 | 가져올 아이디어 | 왜 | 우선순위 | 근거 |
|---|---|---|---|---|
| **LangGraph** | **조건부 라우팅**(출력/점수에 따라 다음 단계 동적 결정) | 지금 패턴은 정적(고정 순서). 스톨감지·열화의 자연스러운 일반화 | 상 | `reports/v3.x-analysis-langgraph-mcp-2026-07-14.md` |
| **LangGraph** | **체크포인트 재개**(중단된 런을 N단계부터 이어서) | 가드 stop·승인 대기·크래시 후 재개 불가. pace-approve와 궁합 | 중 | 〃 |
| **MCP**(Model Context Protocol) | **워커에 실제 도구**(filesystem·git·DB·web) + 설정 주도 확장 | 워커가 텍스트 생산자라 파일을 못 만지는 한계 해소. 의존성0(JSON-RPC) 구현 가능. `backends.json`에 `type:mcp` 자리 있음 | 상(단, 승인 게이트 통합 전제) | 〃 |
| **ACQUIRE**(Know-Before-Fix, 상하이교통대 2026-07-13) | **수정 전 지식 선수집** — Questioner가 지식결손을 질문 2개로 분해→읽기전용 Answerer들이 근거수집→QA를 Resolver에 정적 선주입 | 조기 가설 고정을 막는 순서 분리(해결책 선작성은 오히려 성능↓). SWE-bench +3.8~4.4%p | 상(조건부: opt-in A/B 검증 후 기본화, QA는 knot 저장 금지=BUG-13) | `plans/v4.2.0-plan-acquire-know-before-fix-2026-07-16.md` |

## 규율
- 새 외부 아이디어 이식 시: ① 이 표에 한 줄 ② `reports/`에 assessment/plan ③ 코드에 출처 주석 ④ HISTORY.
- 프레임워크 통째 도입은 원칙적으로 배제(의존성0). 아이디어만 최소 구현으로.
