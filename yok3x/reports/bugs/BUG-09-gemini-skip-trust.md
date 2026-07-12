# BUG-09 · gemini 신뢰 안 된 디렉터리 실행 거부

- **시점**: 2026-07-11 · **심각도**: 높음 · **커밋**: `df05cdf` · **상태**: 수정됨 ✅

## 증상
gemini 백엔드를 실제로 호출하면 **빈 출력 + exit 55**로 실패. stderr:
```
Gemini CLI is not running in a trusted directory. To proceed, either use `--skip-trust`,
set GEMINI_CLI_TRUST_WORKSPACE=true, or trust this directory in interactive mode.
```
gemini 워커(design_review 라우팅·fanout·프로파일 gemini)가 **조용히 실패**하던 상황.

## 근본 원인
gemini CLI 0.44+는 **'신뢰되지 않은 디렉터리'에서 실행을 거부**한다(보안 기능). yok3x는
워커를 격리 cwd나 임의 워크스페이스에서 돌리므로, 커맨드에 `--skip-trust`가 없으면 거부됨.
(codex의 `--skip-git-repo-check`와 같은 부류의 헤드리스 우회 플래그 누락.)

## 진단
빈 임시 dir에서 `gemini -p ... --output-format json` 직접 실행 → exit 55, trust 에러 확인.
`--skip-trust` 추가 재시도 → exit 0, `response` 정상.

## 수정
gemini 백엔드 커맨드에 `--skip-trust` 추가. 겸사 `model_arg`도 추가(프로파일/다운그레이드용).

## 검증
신뢰 안 된 임시 dir에서 `run_backend("gemini")` → ok=True 8.8s, `response` 파싱 + 토큰
집계(`stats.models.*.tokens`) 정상. 출력 포맷이 파서와 일치함도 확인.

## 교훈
각 CLI의 **헤드리스 실행 전제**(신뢰/레포체크/권한)를 확인한다. 조용한 exit-code 실패는
"설치됐는데 왜 안 되지"로 오래 숨는다 — 실제 호출로 exit code·stderr를 본다.
