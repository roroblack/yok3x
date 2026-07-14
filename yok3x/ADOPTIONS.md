# ADOPTIONS.md — 외부 아이디어 채택 원장

yok3x 원칙: **프레임워크는 안 들이고, 외부 연구·도구의 좋은 아이디어만 의존성 0(표준 라이브러리)으로
이식**한다. 무엇을 · 어디서 · 왜 · 어디에 적용했는지를 이 파일에 기록한다. 새 아이디어를 가져오면 여기
한 줄 이상 추가한다(RULE §5.5·§7과 함께). 상세 판단은 각 `reports/*-assessment-*.md`에 있다.

## 채택 완료 (구현됨)

| 출처 | 가져온 아이디어 | 왜 (필요) | 어디에 적용 | 근거 리포트 |
|---|---|---|---|---|
| **Karpathy 4원칙**(에이전트 폭주 방지 운영원칙) | ①작게 나눠 실행 ②사람 승인 ③자가검증 ④예산 상한 | LLM 에이전트가 폭주/무한루프/과금하는 것을 코드 수준 브레이크로 막기 위해 | `orchestrator.py`(단계분할·승인 게이트·SELF-CHECK·요금 가드) | — |
| **ARIS**(적대적 멀티에이전트 검수) | 리뷰어를 '채점'이 아니라 **반증/파괴** 우선으로 + **교차 패밀리** 강제(AD1) | 같은 모델끼리 서로 봐주는 검수의 허점을 없애 결함 검출률을 높이려고 | `orchestrator.py`(ADVERSARIAL_REVIEW·`_ensure_cross_family`), `config.adversarial_review` | `v3.5.0-assessment-aris-adversarial-mode-2026-07-11.md` |
| **LightRAG**(이중레벨 검색) | knot 검색을 **저수준(키워드)+고수준([[링크]]·태그 확장)** 이중레벨로 | 지식그물 검색이 단순 키워드만이면 관련 노트를 놓쳐서 | `knot.query`(dual-level, 의존성0) | `v3.5.0-assessment-lightrag-2026-07-11.md` |
| **Mem0**(프로덕션 장기기억) | **consolidation** 3종 — 요점 추출·중복 통합(lint)·최신성 감쇠(검색 가중) | 런 산출물을 통째로 쌓으면 knot이 비대·중복해져서. 벡터DB 없이 실전 이득만 | `knot.extract_key_points`·`lint`(dedup)·`_recency_weight` | `v3.5.0-assessment-mem0-2026-07-13.md`, `v3.5.0-knot-consolidation-2026-07-13-1600.md` |

## 평가 후 미채택 (프레임워크 통째 도입 배제)

| 출처 | 왜 통째로는 안 가져왔나 | 대신 |
|---|---|---|
| **Mem0 프레임워크**(벡터DB+LLM 추출) | 의존성 0 위배·과설계(주 용도가 장기 다세션 대화가 아님) | 위의 consolidation 패턴만 이식 |
| **LightRAG 프레임워크** | 벡터/그래프 저장 의존성 | 이중레벨 검색 아이디어만 이식 |
| **LangChain/LangGraph 프레임워크** | 의존성·통제/투명성 상실 | 아래 '이식 후보'의 아이디어만 |
| **Zed ACP**(에이전트 클라이언트 프로토콜) | 통합 이점 대비 범위 밖 | 보류 — `v3.4.0-plan-zed-acp-integration-2026-07-11.md` |

## 이식 후보 (분석 완료, 계획/구현 대기)

| 출처 | 가져올 아이디어 | 왜 | 우선순위 | 근거 |
|---|---|---|---|---|
| **LangGraph** | **조건부 라우팅**(출력/점수에 따라 다음 단계 동적 결정) | 지금 패턴은 정적(고정 순서). 스톨감지·열화의 자연스러운 일반화 | 상 | `v3.x-analysis-langgraph-mcp-2026-07-14.md` |
| **LangGraph** | **체크포인트 재개**(중단된 런을 N단계부터 이어서) | 가드 stop·승인 대기·크래시 후 재개 불가. pace-approve와 궁합 | 중 | 〃 |
| **MCP**(Model Context Protocol) | **워커에 실제 도구**(filesystem·git·DB·web) + 설정 주도 확장 | 워커가 텍스트 생산자라 파일을 못 만지는 한계 해소. 의존성0(JSON-RPC) 구현 가능. `backends.json`에 `type:mcp` 자리 있음 | 상(단, 승인 게이트 통합 전제) | 〃 |

## 규율
- 새 외부 아이디어 이식 시: ① 이 표에 한 줄 ② `reports/`에 assessment/plan ③ 코드에 출처 주석 ④ HISTORY.
- 프레임워크 통째 도입은 원칙적으로 배제(의존성0). 아이디어만 최소 구현으로.
