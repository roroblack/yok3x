# BUG-06 · mat 대시보드 실측 라벨 누락 (raw 표시)

- **시점**: 2026-07-10 · **심각도**: 낮음 · **커밋**: `282ba45` · **상태**: 수정됨 ✅

## 증상
claude 라이브 실측(`claude_oauth`) 도입 후, `mat` 대시보드에서 claude의 소스 라벨이
`[실측]`이 아니라 raw 문자열 `claude_oauth`로 떴다.

## 근본 원인
`matview.py`의 소스 라벨 매핑 dict에 `claude_oauth` 키가 없어, `.get(v.source, v.source)`가
매핑 실패 시 **원본 source 문자열을 그대로** 반환했다(신규 타입을 매핑에 추가 안 함).

## 수정
매핑에 `"claude_oauth": "실측"` 추가.

## 검증
`mat` 라이브에서 claude가 `[실측] 5h .. · 7d ..`로 표시 확인.

## 교훈
새 `source`/`type`을 도입하면 **표시 매핑도 함께** 갱신. `.get(x, x)` 폴백은 누락을 조용히
raw 노출로 숨긴다 — 매핑 표를 단일 지점으로 두고 검토.
