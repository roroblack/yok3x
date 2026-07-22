# Claude 라이브 사용량 — statusline 연동 (F-08 / R-01b)

Claude Pro/Max 구독의 5h·7d 사용률과 **실제 리셋 시각**을, **OAuth 토큰이나 Anthropic API 호출 없이**
안전하게 얻는 방법. Claude Code가 statusLine 명령에 stdin으로 넘겨주는 `rate_limits`를 수동 소비한다.
(Anthropic 2026-04-04 정책: 서드파티의 구독 OAuth 사용 차단 → 이 경로가 유일하게 컴플라이언트.)

## 원리
Claude Code는 매 렌더(새 응답·`/compact`·refreshInterval 등)마다 설정된 statusLine 명령을 실행하며
**stdin으로 세션 JSON**을 넘긴다. 그 JSON의 `rate_limits.five_hour/seven_day`에 `used_percentage`와
`resets_at`(epoch초)가 들어 있다. `yok3x statusline`이 이를 `~/.yok3x/statusline.json`에 캐시하고,
`claude_statusline` 프로브가 읽어 사용량 표시·페이싱에 쓴다.

## 설정 (2단계)

### 1) Claude Code `settings.json`에 statusLine 등록
`~/.claude/settings.json`(사용자 전역) 또는 `.claude/settings.json`(프로젝트):
```json
{
  "statusLine": {
    "type": "command",
    "command": "yok3x statusline",
    "refreshInterval": 10
  }
}
```
- `yok3x`가 PATH에 없으면 절대 경로로: `"command": "python /경로/yok3x.py statusline"`.
- `refreshInterval`(초, 최소 1)을 주면 그 주기로도 갱신된다(없으면 이벤트 시에만).
- 이 명령의 stdout이 Claude Code 상태줄에 표시된다(`yok3x 5h 92% · 7d 38%` 형태).

### 2) yok3x claude 프로브 타입
기본값이 이미 `claude_statusline`이다(신규 설치). 기존 설정이면 `yok3x.json`:
```json
{ "limits": { "claude": { "type": "claude_statusline" } } }
```
없거나 만료(기본 900초) 시 자동으로 로컬 트랜스크립트 추정으로 폴백한다.

## 확인
```bash
# 수동 시뮬레이션(실제로는 Claude Code가 호출)
echo '{"rate_limits":{"five_hour":{"used_percentage":92,"resets_at":9999999999},
      "seven_day":{"used_percentage":38,"resets_at":9999999999}}}' | yok3x statusline
yok3x limits          # [claude] type=claude_statusline ok=True 실측 ... 리셋 N시간 후
```

## 함정 (공식 문서 기준)
- `rate_limits`는 **Claude.ai Pro/Max**에서만, 그리고 **세션 첫 API 응답 이후**에 등장한다(그 전엔 없음).
- 각 창(five_hour/seven_day)이 **독립적으로 누락**될 수 있다.
- **API/Enterprise 플랜엔 아예 없다.** OAuth Max 20x 일부 누락 보고(claude-code#40094)도 있다.
- 위 모든 경우 yok3x는 값을 **지어내지 않고** 트랜스크립트 추정으로 폴백한다(RULE §5.5).

## 왜 이 방식인가
- 워커 디스패치(claude CLI 서브프로세스)와 마찬가지로 **1st-party(Claude Code) 출력만 소비** → 정책 안전.
- OAuth 토큰·`api.anthropic.com` 호출·client_id 사칭 **전무**. (이전 `claude_oauth` 경로는 BUG-27로 폐기.)
