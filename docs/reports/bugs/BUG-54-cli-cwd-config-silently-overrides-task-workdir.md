# BUG-54 — CLI 실행 위치가 task.json의 workdir를 조용히 무시하고 다른 config를 로드함

## 증상

`yok3x run <task.json>`을 task.json이 지정한 `workdir` **밖**(예: yok3x 저장소 자체
디렉터리)에서 실행하면, 사전등록된 producer/reviewer backend가 완전히 뒤바뀌고(claude가
실제로는 codex로, codex가 실제로는 claude로 실행) 런당 비용 상한(`max_usd_per_run`)이
무효화된다 — 아무 오류·경고도 없이.

실측(T-2 2차 수집 #8, 2026-08-29): 태스크 workdir가 아니라 yok3x 저장소 안에서
`python yok3x.py run <절대경로>/task.json`을 실행했다가, 저장소 자체의
`active_profile=best`가 개입해 프로듀서/리뷰어 backend가 뒤바뀌었고
`guard.reservation.max_usd_per_run=0`(무제한)이 적용돼 태스크별로 설정한 1.50 상한이
무효화됐다. 실비용 $0.5648의 관측이 사전등록 프로토콜의 고정 조건을 위반해 전량 폐기됐다
(`docs/TODO.md` T-2 "관찰 3" 참고).

## 원인

`cli.py::main()`은 `cfg = Config.load(".")`로 **CLI 실행 위치(CWD)**의 `yok3x.json`을
로드한다. task.json의 `workdir` 필드는 `orchestrator.py`가 워커 프로세스를 실행할 작업
디렉터리를 정하는 데만 쓰이고, config를 어디서 읽을지와는 완전히 무관하다. 사용자(또는
스크립트)가 task.json을 절대경로로 참조하면서 CLI 자체는 다른 디렉터리(특히 여러 프로젝트를
넘나드는 저장소 루트)에서 실행하면, 그 디렉터리의 `yok3x.json`이 조용히 적용된다 —
`active_profile`이 다르면 backend가 바뀌고, `guard.reservation.max_usd_per_run`이 다르면
비용 안전장치가 꺼진다.

## 수정

`cli.py::main()`에서 `run`/`loop` 커맨드일 때, task.json의 `workdir`가 설정돼 있고 그 값이
현재 CWD와 다르면 stderr에 경고를 출력한다(동작은 바꾸지 않는다 — config 로드 경로를
task.json 기준으로 자동 전환하는 것은 더 큰 변경이라 이번엔 하지 않았다). task.json이
없거나 JSON 파싱에 실패해도 이 경고 로직 자체가 크래시하지 않는다(기존 하위 로직이 그
오류를 처리).

## 검증

- `tests/test_yok3x.py`에 4개 테스트 추가: workdir가 CWD와 다르면 경고 출력·경로 포함,
  workdir가 CWD와 같으면 경고 없음, workdir 필드 자체가 없으면 경고 없음, task.json이
  없거나 깨져 있어도 경고 로직이 크래시하지 않음.
- 전체 스위트 664개 중 663 passed·1 skipped(무관한 기존 타이밍 플레이키 테스트 1개는 단독
  실행 시 통과 — 회귀 아님).

## 한계

경고만 하고 자동으로 막지는 않는다 — CWD 기준 config 로드 자체를 바꾸는 것은 기존
스크립트·워크플로를 깰 위험이 있어 이번 범위에 넣지 않았다. 사용자가 경고를 무시하면
여전히 잘못된 config로 실행될 수 있다.
