# BUG-33 · 페이싱 상한이 하루 중 요동/증가 — OAuth current + 트랜스크립트 today 소스 혼합

- **상태**: 수정 완료 (2026-07-23)
- **심각도**: 중간(페이싱 상한 신뢰성 — 사용자 지적 "왜 상한이 올라감? 이거 그냥 버그")
- **영역**: `yok3x/usage.py` `daily_pace_status` 입력 배선 · 호출부 3곳(cli·guiserver·usage.check)

## 증상

claude 하루 상한이 하루 중에 요동쳤다(8.8% → 6.6% → 6.8% …). 특히 **오늘 사용이 늘수록 상한이 거꾸로
올라가는** 비상식적 동작(오늘 1.6%→1.8%일 때 상한 6.6→6.8).

## 근본 원인 (BUG-29의 부작용)

claude 상한 앵커는 `u0 = current − today_used`(오늘 이전 이번주 사용)로, `cap = k·q − u0`가 하루 안에서
안정적이려면 current와 today_used가 **같은 소스**여야 한다(둘 다 커지면 상쇄→u0 불변). 그런데 BUG-29에서
바 게이지와 소스를 맞추려고 **current를 OAuth 7d%(정체·60초 갱신)**로 바꿨는데, `today_used`는 여전히
**트랜스크립트(연속 증가)**였다. 두 소스를 빼니:
- `cap = k·q − (OAuth_current − transcript_today)` → transcript_today가 자랄수록 빼는 값이 줄어 **상한↑**.
- OAuth 갱신 타이밍과 트랜스크립트 증가가 엇갈려 **요동**.

## 수정

`_pace_inputs(cfg, backend, reading, reset_at)` 헬퍼로 소스 일관성을 강제하고 3곳(cli·guiserver·usage.check)이
공유:
- **실측 reading 7d%(OAuth/app-server)면 → `since_reset_known=False`, `today_used=None`**. OAuth는 주간 %만
  주고 일간 분해가 없으므로, codex와 동일한 **하루시작 스냅샷 모델**로 간다(상한 = 하루시작 스냅샷 기준,
  하루 동안 고정; 오늘 사용 = 스냅샷 이후 성장). 트랜스크립트 today를 섞지 않는다.
- reading 없으면 트랜스크립트 since-reset + **같은 소스** token today_used(`since_reset_known=True`).
- 그것도 없으면 롤링 %.

바 게이지-밴드 정렬(BUG-29 목적)은 유지된다(둘 다 OAuth 기반).

## 검증

- 신규 테스트 2: `_pace_inputs`가 OAuth엔 (False,None) 반환 · OAuth 상한이 하루 안 고정(65→66→68%로
  성장해도 상한 불변, 오늘 사용만 증가).
- 전체 스위트 306 passed(신규 2). 라이브 확인.

## 미해결 (별개 — 정확도)

상한 **값** 자체가 낮게(≈5%) 나오는 건 OAuth 7d%(65%)가 트랜스크립트(48.5%)보다 높기 때문. 어느 쪽이
claude.ai 실제와 맞는지는 사용자 확인 대기 — 65%면 상한 5%가 정확(많이 씀), 48%면 OAuth 과대집계라
페이싱 소스를 재검토해야 한다. 이 리포트는 **요동 버그**만 다룬다. 관련: [[BUG-29]](바-밴드 정렬).

## 교훈

두 값을 빼서(차이로) 지표를 만들 땐 **반드시 같은 소스·같은 스케일**이어야 한다. 정확도(어느 소스가 맞나)와
안정성(요동 없음)은 별개 문제 — 정확도 개선(BUG-29: OAuth 채택)이 안정성 회귀를 부를 수 있으니 함께 봐야 한다.
