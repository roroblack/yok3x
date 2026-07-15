# BUG-18 · 스테일 backends.json의 {prompt}(argv) → 멀티라인 잘림(계산기 실패, BUG-10 재발)

- **시점**: 2026-07-15 · **심각도**: 높음 · **커밋**: (가드+재생성) · **상태**: 수정됨 ✅

## 증상
"간단한 계산기 만들어줘"가 계속 실패 — 워커가 `[작업]`만 받은 듯 "어떤 작업을 도와드릴까요?"만 반환.
BUG-13(자기오염) 수정 후에도, cwd 격리 후에도 재발.

## 근본 원인
**BUG-10의 재발** — 경로가 달랐다. `_run_cli`가 프롬프트를 argv로 넘기면 Windows npm `.cmd` 심이
멀티라인 argv를 **첫 줄바꿈에서 잘라** codex/claude가 첫 줄(`[작업]`)만 받는다(BUG-10). BUG-10은
DEFAULT_BACKENDS에서 `{prompt}`를 빼고 stdin으로 고쳤는데, **디스크의 `backends.json`(2026-07-11자)이
옛 `{prompt}` 형식 그대로**라 로드 시 DEFAULT를 덮어썼다:
```
codex: ['codex','exec','--json','--skip-git-repo-check','{prompt}']   ← 스테일
```
→ `has_prompt_arg=True` → 프롬프트가 argv로 → .cmd 심 잘림 → 워커가 첫 줄만 봄.

## 진단
- 프롬프트 캡처: 조립된 프롬프트는 깨끗(204자). 워커 응답만 "작업 없음".
- codex에 **직접** 같은 프롬프트 → 계산기 정상 생성. yok3x `_run_cli` → 3/3 실패(결정적).
- `subprocess.run` 가로채기 → 실제 cmd에 `'[작업]\n계산기…'`가 **argv 요소**로 들어감 + `stdin=DEVNULL`.
  즉 프롬프트가 argv로 전달됨(스테일 command의 `{prompt}` 탓) → .cmd 심 잘림.

## 수정
1. **방어 가드**(`_run_cli`): `{prompt}`가 argv에 있어도 **프롬프트가 멀티라인이면 {prompt} 자리를 빼고
   stdin으로** 넘긴다. 스테일 config여도 잘림 원천 차단.
2. **backends.json 재생성**: 현재 DEFAULT_BACKENDS로 덮어써 `{prompt}` 제거(stdin 방식).
3. (부수) 잘못 설정된 전역 workspace(=yok3x 레포) 비움. 참고: 초기엔 'workdir==레포면 격리' 자기오염
   가드도 넣었으나, BUG-18 수정 후 cwd=레포여도 정상 작동함을 확인 + 가드가 workdir==root인 테스트를
   오탐해 **가드는 제거**. 진짜 원인은 argv 잘림이었다.

## 검증
재생성+가드 후 codex가 실제 계산기 코드 생성 확인. 회귀 테스트
`test_multiline_prompt_uses_stdin_even_with_stale_prompt_arg`.

## 교훈
- **DEFAULT를 고쳐도 디스크의 스테일 설정이 덮어쓴다.** 코드 수정과 함께 persist된 config의 마이그레이션
  /방어가 필요하다(BUG-02 버전 드리프트, BUG-13 스테일 brief 계열).
- 방어를 **동작 지점(`_run_cli`)에** 둬 스테일/사용자 config에도 견고하게. (BUG-10과 동일 근본, 다른 유입경로)
