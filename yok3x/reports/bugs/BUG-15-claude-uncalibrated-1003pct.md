# BUG-15 · claude 미보정 추정 1003% 오표시(live 429/토큰만료 시)

- **시점**: 2026-07-13 · **심각도**: 중간(오표시·false-stop 위험) · **커밋**: `5811eb3` · **상태**: 수정됨 ✅

## 증상
대시보드가 claude를 `추정 5h 1003.6% · 7d 778.1%`로 표시. 실제 사용량이 아닌 비현실적 값.

## 근본 원인
claude 실측(OAuth `/usage`)이 **HTTP 429(호출 과다) 또는 토큰 만료**로 실패 → 트랜스크립트 **추정**
으로 폴백. 그런데 추정은 **캐시read 토큰까지 합산**해 max5x 캡(40M) 대비 401M = 1003%로 과대집계.
미보정(real=False)인데도 그 값을 그대로 표시(그리고 hard_ratio 초과로 **false-stop** 위험).

## 진단
토큰 만료(179분 남았는데 429)를 확인. `_probe_claude_oauth`가 live 실패 → est(ok=True, 1003%)를
그대로 반환하는 경로를 특정.

## 수정
1. `check_backend`: 미보정 추정(real=False)이 ratio>3.0이면 **정지 유보(warn)** + calibrate 권장(false-stop 방지).
2. `_probe_claude_oauth`: 추정 ratio>2.0(미보정 확실)이면 **표시하지 않고 원장(sane) 폴백**.
3. (후속) **stale-while-error**: live 일시 실패 시 원장으로 깜빡이지 말고 마지막 실측을 `⚠N분 전 실측`으로
   유지(`max_stale_sec`). → BUG 계열 UX 개선.

## 검증
429 상황에서 최종 표시 = `원장 $0/$5 0%`(1003% 대신). 회귀 테스트
`test_implausible_estimate_is_dropped_for_ledger`, `test_uncalibrated_estimate_does_not_hard_stop`.

## 교훈
**미보정 추정치를 실측처럼 하드 신호(정지)로 쓰면 안 된다.** 신뢰도(real 여부)를 판정에 반영하고,
비현실적 값은 조용한 표시 대신 확실한 폴백(원장)이나 명시적 stale로 강등한다. (관련: RULE §5.5)
