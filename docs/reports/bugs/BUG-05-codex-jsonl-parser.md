# BUG-05 · codex 0.144 JSONL 파서 비호환 (SCORE 미파싱)

- **시점**: 2026-07-10 · **심각도**: 높음 · **커밋**: `c898105` · **상태**: 수정됨 ✅

## 증상
codex 설치 후 리뷰어(codex-critic)가 실행은 되는데 **SCORE가 파싱되지 않음**(`score=None`).
스텝 요약에 raw JSONL(`{"type":"turn.completed","usage":{...}}`)이 그대로 남음.

## 근본 원인
`backends._parse_codex`가 codex **구버전 스키마**(`item.item_type in ("agent_message",
"assistant_message")`)만 봤다. codex 0.144는 agent 메시지를 **`item.completed` 이벤트의
`item.type == "agent_message"` / `item.text`**로 준다. 필드명이 `item_type`→`type`로 바뀌어
파서가 어시스턴트 텍스트를 못 뽑고, 폴백으로 마지막 줄(usage JSON)을 텍스트로 잡았다.

## 진단
실제 codex 0.144 `exec --json` 출력을 캡처해 이벤트 타입 확인:
`thread.started · turn.started · item.completed · turn.completed`. 메시지는
`item.completed`의 `item.text`("SCORE: 8"), 사용량은 `turn.completed.usage`에 있음을 확정.

## 수정
`itype = item.get("type") or item.get("item_type")`로 신·구 스키마 **둘 다 인식**.
사용량 집계는 기존 `turn.completed.usage` 경로가 이미 동작.

## 검증
실제 캡처 포맷으로 단위 테스트(`test_parse_codex_new_item_completed_schema`) — 텍스트 추출 +
토큰 집계 확인. 라이브 producer-reviewer에서 codex `score=7.0` 파싱 확인.

## 교훈
외부 CLI의 JSON 스키마는 버전마다 바뀐다. 파서는 **실제 출력을 캡처해 맞추고**, 신·구 필드를 함께 인식한다.
