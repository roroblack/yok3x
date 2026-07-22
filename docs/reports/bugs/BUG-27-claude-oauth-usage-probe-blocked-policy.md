# BUG-27 · claude 사용량 프로브가 정책 차단된 OAuth 엔드포인트를 60초마다 호출

- **상태**: 수정 완료 (2026-07-22)
- **심각도**: 높음(계정 안전 — 서드파티 구독 OAuth 사용은 2026-04-04 Anthropic 정책 위반·429)
- **영역**: `yok3x/config.py` claude limits 기본값 · live `yok3x.json` · `limits.py` OAuth 경로

## 증상

claude limits 프로브 `type=claude_oauth` + `auto_refresh=True`로, `https://api.anthropic.com/api/oauth/usage`를
구독 OAuth 토큰으로 **60초마다 직접 호출**하고 refresh_token까지 회전했다. 응답은 **HTTP 429**(호출 과다/차단).

## 근본 원인

**Anthropic 2026-04-04 정책**: 서드파티 에이전트/하네스의 Claude Pro/Max 구독 사용을 차단하고 **OAuth 토큰
인증을 자사 제품(Claude.ai·Claude Code)으로만 제한**(웹 검증: decodethefuture 등). yok3x의 사용량 프로브는
공식 CLI를 감싸는 허용 경로가 **아니라**, OAuth 토큰으로 usage 엔드포인트를 직접 때리는 **차단 대상 경로**였다.
(외부 리서치 리포트 R-01이 지목 → 코드·정책 교차검증.)

## 수정

claude 프로브를 **로컬 트랜스크립트 추정(`claude_transcripts`)**으로 전환하고 `auto_refresh`를 끔.
`~/.claude/**.jsonl` 토큰만 합산(네트워크 호출 0). **라이브 실측은 이미 429로 죽어 있어 기능 손실 0**,
계정 리스크만 제거. config.py 기본값·live yok3x.json 모두 전환. `claude_oauth` 코드 경로는 남겨둠(명시 opt-in용).

## 검증

- `yok3x limits`: claude `type=claude_transcripts`, "live실패(429)" 사라짐(OAuth 호출 중단).
- 전체 스위트 285 passed(claude_oauth 테스트는 명시 config로 직접 호출하므로 기본값 변경에 불변).
- GUI 서버 재기동으로 새 config 반영.

## 후속(계획서 R-01b)

라이브 사용량이 필요하면 **F-08**: Claude Code가 statusline 명령에 stdin으로 넘기는 `rate_limits.five_hour/
.seven_day` JSON을 읽는다(1st-party 경로, OAuth API 호출 없음). 함정: `resets_at` epoch/ISO 두 형식·일부
플랜(API/enterprise/일부 OAuth Max) 필드 누락(claude-code#40094) — 방어 필요.

## 후속 (2026-07-22 · 재조사 후 안전 재활성)

초기 대응(전면 폐기)은 과했다. 재조사: OAuth usage 429는 **버그**(claude-code #31637)지 밴 아니고,
4-4 서드파티 차단은 **6-16 철회**(Anthropic: 서드파티 구독 사용 그대로). → 밴 위험 낮음(단 정책 보장 없는
unsupported access). codex 공동설계로 **A+B 계층형** 재도입: OAuth(auto_refresh off·**실패 백오프**:
429 지수/401·403 장기중단) 우선 → 실패 시 transcripts+**주간 위상**(실측 7d 리셋을 `weekly_reset_epoch`에
저장·전개) 폴백. 하드 두들김·client_id 사칭 없이 라이브를 회복하고, 실패해도 7d 리셋 카운트다운 유지.
(기본값은 보수적으로 `claude_statusline` 유지, 헤드리스 라이브는 `claude_oauth` 권장.)

## 교훈

워커 디스패치(공식 CLI 서브프로세스)는 1st-party 경유라 안전하지만, **사용량 프로브가 OAuth 토큰을 직접 쓰면
별개의 정책 위반 경로**가 된다. 벤더 정책 변화는 코드가 아니라 운영 안전 문제 — 외부 신호(429·정책 공지)를
데이터로 받아 교차검증해야 한다. 관련: [[BUG-17]](토큰 갱신), [[BUG-26]](페이싱), F-08(statusline 대체재).
