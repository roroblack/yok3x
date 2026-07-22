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

새 파라미터 `since_reset_known`(current가 이번주 실측이면 True=claude, 롤링 %면 False=codex)을 도입하고,
codex(False)는 **하루 시작 시점 스냅샷(`start_pct`)** 기준으로 오늘 사용·상한 앵커를 낸다:
- **주간 첫날 판정** `is_day1` = (하루 시작 == 마지막 리셋). 첫날은 하루 시작이 곧 리셋이라 '오늘 이전 사용'이
  0이다. 롤링 %는 창 안에서 since-reset ≈ 현재값이므로, **첫날 `start_pct=0` → 오늘 = 현재 롤링%, 상한 =
  기준 q 온전**. 이후 날은 하루 시작 롤링%가 기준선(그만큼 상한 조여짐).
- 오늘 사용(codex) = `max(0, current − start_pct)`(스냅샷 대비, 롤오프 하락도 자기보정). 상한 `u0`도 `start_pct`.
- 호출부 3곳(`cli.py`·`guiserver.py`·`usage.py`)이 `weekly_used_since_reset` 반환이 `None`인지로
  `since_reset_known`을 판정해 전달.

claude 경로 완전 불변(`since_reset_known` 기본 True, 오늘 사용은 토큰 기반 `today_used`). 낡은 codex
`pace.json` 레코드 1회 삭제 → 재초기화.

## 증상 2 (같은 근본원인, 사용자 2차 지적)

상한을 `week_used`로 고쳐도 codex "오늘"이 **0으로 표시**됐다(리셋 직후 5% 썼는데 0/9 아니라 5/14여야).
원인: 오늘 사용의 기준선(`start_pct`)을 `current`로 잡아, 관측 시작 시점에 이미 5%면 그 5%가 '오늘 이전'으로
치부됐다. codex 리셋은 오늘 11:01(당일)이라 그 5%는 전부 '오늘' 사용이 맞다 → 첫날 `start_pct=0`로 교정.

## 증상 3 (claude, 같은 앵커 원리 — 사용자 3차 지적)

claude에서 "오늘 1%p / 상한 23.5%p"가 오늘 쓸수록 상한이 계속 바뀌었다. 원인: 상한 앵커 u0가
`weekly_used_since_reset`(=**오늘 사용분 포함**)이라, 오늘 쓸수록 u0가 올라 `상한=k·q−u0`가 깎였다.
상한은 '오늘 이전 사용'을 기준으로 하루 안에서 안정적이어야 한다. 수정: claude(토큰)는 앵커를
`current − today_used`(오늘 제외)로 **매 폴 재계산** — 이 값은 과거 데이터라 안정적이고 오늘 사용이 상한을
깎지 않는다. codex는 이미 하루시작 스냅샷(start_pct)이라 동일 원리. (표시값이 사용자 기대와 다른 것은 claude
수치가 `real=False` 전사 추정이기 때문 — `yok3x calibrate`로 정밀화 권장. 이건 별개.)

## 검증

- `test_daily_pace_codex_day1_anchors_at_reset`: 첫날 5% → **오늘 5 / 상한 14**; 3일차 30% → 오늘 0 / 상한 12.
- `test_daily_pace_cap_excludes_today_and_is_stable`: claude 오늘 1.1→3.0으로 늘어도 상한 **24.6 불변**(옛 방식은
  23.5→21.6 계속 깎임).
- 실측: codex `오늘 5/14`, claude 상한 오늘 제외 시 24.6(안정). 전체 스위트 182 passed.

## 교훈

리셋 앵커 페이싱은 **"이번 창에서 쓴 양"**을 기준으로 해야 한다. 롤링 창 %는 직전 창 사용이 섞일 수 있어
그대로 since-reset 대용으로 쓰면 상한이 과거에 오염된다. 토큰이 없는 백엔드는 롤링%가 창 안에서 since-reset을
근사한다는 점을 이용하되, **관측 시작 시점의 잔량을 '오늘'로 오인하지 않도록 하루/창 경계(특히 첫날=리셋)에서
기준선을 명시**해야 한다. 관련: F2-10(codex 페이싱 정밀도), [[BUG-12]](codex 창 오라벨), [[BUG-20]](사용량 스트립).
