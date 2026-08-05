# BUG-43 · `_kill_tree`의 `capture_output=True`가 GUI 서버를 통째로 멈춤

- **상태**: 수정 완료 (2026-08-05)
- **심각도**: **높음** — GUI 서버가 살아있고 포트도 리슨 중인데 모든 요청이 무한 대기(실사용자 영향 직결)
- **영역**: `yok3x/limits.py` `_kill_tree`(codex app-server 정리)
- **발견 경로**: 사용자 실측 지적("켜놔도 자꾸 꺼진다") → 라이브 모니터링 + `py-spy` 스택 덤프로 재현

## 증상

GUI를 켜놔도 시간이 지나면 페이지가 응답하지 않는다("꺼져 있다"). 사용자 관찰: 다른 곳에서 끄는 건가?

라이브 진단:
- 프로세스(PID)는 살아있고, `netstat`에도 8760 리슨 소켓이 존재.
- 그런데 `curl`은 **몇 번을 재시도해도 타임아웃**(HTTP 000) — TCP는 열려 있는데 응답이 안 옴.
- `py-spy dump --pid <gui>` 로 실제 스레드 스택을 뜬 결과, 요청 처리 스레드가 다음에서 멈춰 있었다:
  ```
  _wait_for_tstate_lock (threading.py:1169)
  join (threading.py:1153)
  _communicate (subprocess.py:1628)
  communicate (subprocess.py:1209)
  run (subprocess.py:550)
  _kill_tree (yok3x/limits.py:446)
  _appserver_rate_limits (yok3x/limits.py:438)
  ```
- `tasklist`로 확인하니 `codex.exe` 3개·`codex-code-mode-host.exe` 1개가 **고아로 누적**돼 있었다.

## 근본 원인

`_kill_tree`가 codex app-server 프로세스를 정리할 때:
```python
subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
               capture_output=True, timeout=5)
```
`capture_output=True`는 stdout/stderr를 파이프로 읽는 **리더 스레드**를 만든다. codex app-server가
남긴 손자 프로세스(위 4개)가 그 파이프의 쓰기 핸들을 **상속**해서 계속 들고 있으면, `taskkill.exe`
자체는 금방 끝나도 파이프의 읽기 쪽은 **EOF를 영원히 못 받는다.**

`subprocess.run(..., timeout=5)`가 지정돼 있지만 이 타임아웃은 프로세스 종료 대기에만 적용된다.
타임아웃이 지나 `TimeoutExpired`를 던지기 **직전**, CPython의 내부 정리 경로가 이미 받은 출력을
모으려고 그 리더 스레드를 **타임아웃 없이 `join()`** 한다 — 그래서 지정한 5초가 무의미해지고
사실상 무한 대기가 된다. 잘 알려진 Windows subprocess 함정이다.

`build_state()`가 한 요청 안에서 codex 상태까지 확인해야 완성되므로, **새로 들어오는 요청도 각자
이 지점에서 다시 멈춘다** — ThreadingTCPServer가 요청마다 새 스레드를 만들어도 전부 같은 함정에
빠지니, 서버 전체가 살아있는 채로 응답 불능이 된다.

## 수정

이 호출은 taskkill의 출력이 필요 없다. `capture_output=True` → `stdout=subprocess.DEVNULL,
stderr=subprocess.DEVNULL`로 바꿔 **파이프 자체를 만들지 않는다.** 리더 스레드가 없으니 손자
프로세스가 핸들을 물고 있어도 무관 — 이 경로가 원천 차단된다.

## 즉시 복구 조치 (진단 중 실시)

멈춰 있던 서버를 고아 프로세스 4개를 강제 종료해 즉시 풀었다(파이프 쓰기 핸들이 닫히자 막혀 있던
`join()`이 즉시 풀리며 요청이 완료됨 — 진단의 인과관계를 실측으로 확인). 코드 수정 후에는 이
수동 조치 없이도 재발하지 않는다.

## 검증

- 신규 테스트 `test_kill_tree_never_captures_output_on_windows`: `_kill_tree`가 `taskkill`을
  `stdout=DEVNULL, stderr=DEVNULL`로만 호출하고 `capture_output`을 쓰지 않는지 확인(회귀 방지).
- 라이브 재현: 고아 프로세스 킬 → 멈춰 있던 요청이 즉시 완료(HTTP 200) 확인.
- 370 passed.

## 남은 위험(정직 표기)

`taskkill /T`(트리 킬)가 왜 애초에 그 손자 프로세스들을 못 잡았는지는 별개 문제다 — codex CLI가
내부적으로 프로세스를 분리(detached) 실행하면 Windows가 부모-자식 관계를 못 추적해 `/T`로 못
잡을 수 있다. 이번 수정은 **그 상황에서도 서버가 멈추지 않게** 하는 것이지, 고아 프로세스 발생
자체를 막지는 못한다(느리게 쌓일 수 있음 — 별도 후속 과제로 남긴다).

## 교훈

`subprocess.run(..., timeout=N)`의 타임아웃은 **프로세스 종료**엔 적용되지만, `capture_output`/
`stdout=PIPE`로 출력을 모으는 내부 정리 경로(리더 스레드 join)까지 항상 그 타임아웃 안에 끝난다는
보장은 없다(특히 Windows + 손자 프로세스의 핸들 상속 조합에서). **출력이 필요 없는 정리성
subprocess 호출은 `DEVNULL`을 써서 파이프/리더 스레드 자체를 만들지 말 것.** 서버가 "떠 있는데
응답이 없다"는 증상은 크래시가 아니라 **락/join 데드락**을 의심하고, `py-spy` 같은 도구로 실제
스레드 스택을 뜨는 게 로그만 보는 것보다 훨씬 빠르게 원인을 특정해준다.
관련: [[BUG-39]](관측 코드가 본작업을 죽임), [[BUG-42]](서브프로세스·격리 관련 완화).
