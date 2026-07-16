# BUG-17 · 토큰 자체갱신 기본 엔드포인트 오류 + User-Agent 누락 → 항상 실패

- **시점**: 2026-07-15 · **심각도**: 중간(기능 미작동, 단 fail-safe) · **커밋**: (엔드포인트/UA 수정) · **상태**: 수정됨 ✅

## 증상
`auto_refresh`를 켜고 라이브 검증하니 갱신이 항상 실패:
- User-Agent 없이 → **403**(WAF 차단)
- `https://console.anthropic.com/v1/oauth/token` → **404**(엔드포인트 없음)

## 근본 원인
1. **엔드포인트 오류**: 기본 `token_url`을 `console.anthropic.com/v1/oauth/token`으로 추측 → 실제로는
   `https://api.anthropic.com/v1/oauth/token`(작동하는 usage 프로브와 같은 호스트 계열). 추측값이 틀렸다.
2. **User-Agent 누락**: 갱신 요청에 UA 헤더가 없어 엔드포인트 WAF가 403. usage 프로브는 이미
   `User-Agent: claude-cli/...`를 보내는데 갱신은 안 보냈다.

## 진단
만료된 토큰(무해)으로 헤더·엔드포인트 변형을 실험 → UA 추가 시 403→404, 엔드포인트를
`api.anthropic.com/v1/oauth/token`으로 바꾸니 **200**(access_token·refresh_token·expires_in 반환). 확정.

## 수정
- `config.limits.claude.token_url` = `https://api.anthropic.com/v1/oauth/token`(라이브 검증됨).
- `_refresh_claude_token` 요청에 `User-Agent`·`anthropic-beta` 헤더 추가(usage 프로브와 동일).

## 검증
올바른 엔드포인트+UA로 200 응답 확인(회전 refresh_token·expires_in 포함). 목 테스트 3 통과.

## 교훈·2차 사고
- **추측한 엔드포인트/헤더는 라이브로 검증해야 한다.** fail-safe 설계 덕에 무해했지만 기능은 죽어 있었다.
- **2차 사고(운영 교훈)**: 라이브 검증 시 수동 스크립트가 성공 응답의 새 refresh_token을 **저장하지 않아**,
  회전된 토큰이 유실되고 저장분이 무효화됨 → 사용자 재로그인 필요. **회전 토큰은 성공 즉시 저장**해야 한다
  (제품 코드 `_write_oauth_atomic`은 그렇게 하지만, 검증용 수동 호출이 규율을 어겼다). 검증도 저장 경로로.
