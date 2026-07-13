# BUG-12 · codex 한도 창 오라벨 (7일을 5h로 표시, 주간 수치 사라짐)

- **시점**: 2026-07-11 · **심각도**: 중간 · **커밋**: (이 커밋) · **상태**: 수정됨 ✅

## 증상
mat/GUI에서 codex가 **`5시간 0% (리셋 6일 23시간 후)`** 로 표시 — 5시간 창인데 리셋이 6일이라
어긋나고, **7일(주간) 줄이 사라졌다**. 얼마 전엔 `5시간 19% · 7일 3%`로 정상이었다.

## 근본 원인
`_probe_codex_appserver`(및 stale 폴백 `_probe_codex_sessions`)가 app-server 응답의
**`primary`→"5h", `secondary`→"7d"로 위치 고정 매핑**했다. 그러나 codex의 실제 응답은
상황에 따라 `primary`에 다른 창을 담는다. 실측 raw:
```json
"primary":   {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": ...},  // 10080분 = 7일!
"secondary": null
```
즉 `primary`가 **7일 창(10080분)** 인데 코드가 "5h"로 라벨 → "5시간인데 리셋 6일"이 되고,
`secondary=null`이라 7일 줄이 없어졌다. (5h 창이 0%라 codex가 그 순간 주간만 primary로 보고.)

## 진단
`limits.probe("codex")`가 창 1개(name='5h', reset 6d23h)만 반환함을 확인 →
`_appserver_rate_limits` raw 덤프로 `primary.windowDurationMins=10080`(7일)임을 특정.

## 수정
창 이름을 **위치(primary/secondary)가 아니라 `windowDurationMins` 길이로 유도**
(`_window_name`: 300→5h, 10080→7d, 그 외 시간/일). app-server·sessions 두 경로 모두 수정.

## 검증
수정 후 codex `7d 0% (리셋 6일 23시간 후)`로 정확히 표시. 단위 테스트:
`_window_name` 매핑 + `primary=7d/secondary=5h`로 와도 길이로 정확히 라벨(`test_codex_appserver_labels_windows_by_duration`).

## 교훈
외부 API의 배열/슬롯 **위치에 의미를 고정하지 마라**. codex는 primary/secondary에 어떤 창이든
담을 수 있다 — 각 세그먼트의 **자기 기술(windowDurationMins)로 판단**한다.
