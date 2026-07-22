# BUG-26 · codex 하루 상한이 7d 롤링 %에 앵커돼 계속 줄어듦

- **상태**: 수정 완료 (2026-07-22)
- **심각도**: 중간(codex 페이싱 표시가 리셋 기준으로 갱신 안 됨 — '상한만 줄고 사용량만 늘고')
- **영역**: `yok3x/usage.py` `daily_pace_status` · 호출부 `cli.py`·`guiserver.py`·`usage.py`

## 증상 (사용자 지적)

claude는 "오늘 0.6%p / 상한 26.2%p"처럼 리셋 시점에 맞춰 오늘 사용이 초기화되는데, codex는
"오늘 0%p / 상한 12%p"에서 **상한만 계속 줄어들고 사용량만 늘어가며** 리셋 시점 기준 갱신이 안 됐다.
`pace.json` 실측: codex `start_pct=4.0, cap_today=10.0`(=14−4), claude `start_pct=15.8, cap_today=26.2`.

## 근본 원인

상한(catch_up/spread)은 앵커 사용량 `u0`를 기준으로 `k·q − u0`를 낸다. claude는 `u0`에 **토큰 기반
이번주(리셋 이후) 실사용**(15.8%)을 쓴다. 그러나 codex는 토큰 로그가 없어(`weekly_used_since_reset`가
`None`) 호출부가 **7d 롤링 %(precise_weekly_pct)**를 그대로 `current`로 넘겼고, 상한이 `u0 = 롤링 %`로
계산됐다. 롤링 %는 **과거(직전 창) 사용까지 포함**해 서서히 오르므로 `상한 = 14 − 롤링%`가 계속 줄었다.
게다가 codex 주간 창이 자주 리셋돼(OpenAI 잦은 글로벌 리셋) 항상 "주 첫날(k=1)"에 머물러 catch-up
증가분도 붙지 않았다. 결과적으로 새 창이 시작돼도 롤오프가 덜 된 롤링 %가 상한을 계속 깎았다.

## 수정

`daily_pace_status`에 **주간 창 이후 누적 증분 `week_used`**를 도입:
- 창(`win`=reset epoch)이 바뀌면 `week_used=0`으로 초기화, 아니면 `current`의 양의 증분을 누적. 하루
  경계를 넘어도 유지(주 단위 측정).
- 새 파라미터 `since_reset_known`: `current`가 이번주 실측이면 True(claude), 롤링 %면 False(codex).
  **False면 상한 앵커 `u0`를 롤링 %가 아니라 `week_used`로 사용.**
- 호출부 3곳(`cli.py`·`guiserver.py`·`usage.py`)이 `weekly_used_since_reset`의 반환이 `None`인지로
  `since_reset_known`을 판정해 전달.

claude 경로는 완전히 불변(`since_reset_known` 기본 True). 낡은 codex `pace.json` 레코드는 1회 삭제해
새 로직으로 재초기화.

## 검증

- 신규 테스트 `test_daily_pace_codex_anchor_since_reset_not_rolling`: 창 A에서 20→30% 누적 후 **새 창 B로
  리셋(롤링 아직 30%)** → codex 상한 **14**(week_used=0), 대조로 claude(since-reset 30%)는 상한 **0**.
- 실측 CLI·직접호출 모두 codex `cap 14`(이전 버그값 9=14−5) 일치, 레코드 `week_used=0/week_last=5`.
- 전체 스위트 181 passed(신규 1 포함).

## 교훈

리셋 앵커 페이싱은 **"이번 창에서 쓴 양"**을 기준으로 해야 한다. 롤링 창 %는 직전 창 사용이 섞여 있어
since-reset 대용으로 쓰면 상한이 과거에 오염된다. 토큰이 없는 백엔드는 공식 % 를 **창 경계에서 스냅샷하고
증분만 누적**해 since-reset을 근사할 수 있다(정수 단위로 거칠지만 방향은 정확). 관련: F2-10(codex 페이싱
정밀도), [[BUG-12]](codex 창 오라벨), [[BUG-20]](사용량 스트립).
