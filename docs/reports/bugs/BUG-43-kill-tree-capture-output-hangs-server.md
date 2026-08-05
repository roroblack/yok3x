# BUG-43 · `_kill_tree`의 `capture_output=True`가 GUI 서버를 통째로 멈춤

- **상태**: 수정 완료(근본원인까지, 2026-08-05) — 1차 수정(무한 대기 차단) 후 사용자가 "근본 해결
  맞냐"고 재지적해 2차로 고아 프로세스 발생 원인(부모-자식 연결 단절)까지 마저 해결
- **심각도**: **높음** — GUI 서버가 살아있고 포트도 리슨 중인데 모든 요청이 무한 대기(실사용자 영향 직결)
- **영역**: `yok3x/limits.py` `_kill_tree`·`_appserver_rate_limits`(codex app-server 정리)
- **발견 경로**: 사용자 실측 지적("켜놔도 자꾸 꺼진다") → 라이브 모니터링 + `py-spy` 스택 덤프로 재현.
  1차 수정 후 사용자가 "이거 맞아? 근본적인 해결이 아닌 거 같은데?"라고 재지적 → 프로세스 트리
  실측 재조사로 2차 원인(진짜 근본원인) 발견.

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

## 후속 — 고아 프로세스 근본원인도 확인·해결 (2026-08-05, 사용자 지적으로 재조사)

최초 수정 직후 사용자가 "이게 근본 해결이 맞냐"고 지적. 맞는 지적이었다 — 위 수정은 **서버가
안 멈추게** 했을 뿐, **고아 프로세스가 생기는 것 자체**는 여전했다. 라이브로 재조사했다.

**추가 근본원인**: `codex`는 `shutil.which()`로 `codex.cmd`(npm 셈)에 풀린다. `Popen([exe]+args)`가
잡는 `proc.pid`는 **cmd.exe 래퍼의 PID**일 뿐이다. 실제 `codex.exe`/`codex-code-mode-host.exe`는
`node.exe`를 거쳐 뜨는데, **`_kill_tree`가 실행되는 시점엔 이미 그 부모-자식 연결이 끊겨 있다**
(실측: 방금 띄운 cmd.exe의 PID와 그 직후 뜬 codex.exe의 부모 PID가 서로 다름을 `Win32_Process`
스냅샷으로 직접 확인). 그래서 `taskkill /F /T /PID <cmd.exe pid>`가 트리를 다시 훑어도 진짜
`codex.exe`가 애초에 안 보인다 — `/T`가 실패하는 게 아니라 **볼 수 있는 트리에 대상이 없는 것**.
타이밍에 따라 되기도/안 되기도 해 간헐적으로 관측됐다(고아 4개 누적을 실측).

**수정**: Windows **Job Object**로 컨테인. `codex.cmd` 프로세스를 띄운 직후(가능한 한 빨리) Job
Object에 편입해두면, 이후 몇 단계를 거쳐 태어나는 자손(node→codex.exe→codex-code-mode-host.exe)도
**자동으로 같은 job에 속한다**(Windows 커널이 job 소속을 프로세스 생성 시점에 상속 — PID 추적에
의존하지 않음). `TerminateJobObject` 한 번으로 몇 단계였든 트리 전체가 죽는다. `_kill_tree`는
job이 있으면 이걸 **먼저** 호출하고, 기존 `taskkill`은 job이 없거나 실패했을 때의 폴백 + 이중
안전망으로 유지한다. job 생성·편입 실패는 예외를 삼키고 조용히 taskkill-only 경로로 내려간다
(비Windows에서도 즉시 no-op).

**검증(둘 다 실측)**:
- 독립 스크립트로 실제 `codex.cmd` 프로세스를 job에 편입 후 `TerminateJobObject` → 킬 전 있던
  codex.exe가 킬 후 사라짐 확인(같은 job에 없던 기존 고아는 당연히 안 건드려짐 — 통제 확인).
- 테스트 `test_job_object_really_kills_a_real_process_tree`(모킹 아님, 실제 Windows 전용):
  `cmd.exe → python.exe` 실제 트리를 만들어 job 편입 후 `TerminateJobObject`로 **둘 다** 죽는지
  `tasklist`로 직접 확인 — 스위트 안에서도 재현.
- 신규 유닛 테스트 4(비Windows no-op·job 우선순위·job 없을 때 폴백·편입 실패 시 정리) 추가.

## 후속의 후속 — job 편입 자체의 경쟁(race) 발견·해결 (2026-08-05, 라이브 모니터링 중 재발 관측)

Job Object 배포 직후 라이브 모니터가 **새 고아**를 잡아냈다(`codex 관련 프로세스 3개→4개`). 재조사:
job 편입은 `Popen()` 직후 **한 번만** 실행되는데, 그 편입이 끝나기도 전에(마이크로초~수백ms 사이)
직계 자식이 이미 손자 프로세스를 만들어버리면 그 손자는 job 소속을 상속받지 못한다 — 시스템 부하가
있을 때(그 순간 py-spy·tasklist·powershell로 부하를 준 상태) 이 경쟁이 실제로 드러났다.

**시도했으나 막힌 길**: `CREATE_SUSPENDED`로 프로세스를 만들어 job 편입 후 재개하면 경쟁이
원천 차단되지만, CPython의 `subprocess.Popen`은 내부적으로 메인 스레드 핸들을 생성 직후 즉시
닫아버려(`ht.Close()`) 표준 API로는 나중에 재개(`ResumeThread`)할 방법이 없다 — 확인 후 포기.

**채택한 해법**: `_appserver_rate_limits`가 도는 동안 **지속적으로 스윕**하는 데몬 스레드
(`_win_start_job_sweeper`)를 추가. `CreateToolhelp32Snapshot`(순수 ctypes, 서브프로세스 호출 없이
빠름)으로 전체 프로세스의 {pid: ppid}를 0.15초마다 스냅샷 떠서, 이미 job에 속한 PID의 새 자손을
찾으면 즉시 마저 편입한다(여러 단계를 한 틱 안에 다 따라잡도록 반복). 완벽한 이론적 보장(그러려면
CREATE_SUSPENDED가 필요)은 아니지만, 실측된 경쟁 창(수백ms)을 촘촘히 커버해 실질적으로 충분하다.

**검증**:
- **결정론적 재현 테스트**(`test_job_sweeper_catches_grandchild_that_already_won_the_race`): 손자가
  **job 편입보다 먼저 뜨도록 일부러 기다린 뒤에야** job을 만들어, "이미 진 경쟁"을 강제 재현 —
  스위퍼가 그래도 그 손자를 찾아 편입해 `TerminateJobObject`로 같이 죽는지 확인. 통과.
- **실전 스트레스**: 실제 codex 바이너리로 **6회 연속** app-server 프로브를 돌려, 시작 전/후
  프로세스 목록이 **완전히 동일**(새 고아 0개)함을 확인.
- 신규 테스트 2 추가(총 8개). 376 passed.

**교훈**: "한 번 편입하면 끝"이라는 가정 자체가 틀렸다 — 프로세스 생성은 비동기이고, 자손이
얼마나 빨리 태어날지는 시스템 부하에 좌우된다. 단발성 조치보다 **관찰 기간 내내 지속되는 방어**
(sweep-until-done)가 타이밍에 의존하는 경쟁을 실질적으로 닫는 더 견고한 패턴이다.

**남은 정직한 한계**: 이론상 CREATE_SUSPENDED 없이는 100% 수학적 보장은 아니다(스윕 틱 사이의
극히 짧은 창은 여전히 이론적으로 존재). Job Object 자체가 생성/편입에 실패하는 극단 상황(권한
문제, 중첩 job이 막힌 구버전 Windows 등)에서는 taskkill-only 폴백으로 내려간다. 다만 실측(6회
연속 스트레스 + 결정론적 최악 시나리오 재현 둘 다 통과)으로 실질적 충분성을 확인했다.

## 교훈

`subprocess.run(..., timeout=N)`의 타임아웃은 **프로세스 종료**엔 적용되지만, `capture_output`/
`stdout=PIPE`로 출력을 모으는 내부 정리 경로(리더 스레드 join)까지 항상 그 타임아웃 안에 끝난다는
보장은 없다(특히 Windows + 손자 프로세스의 핸들 상속 조합에서). **출력이 필요 없는 정리성
subprocess 호출은 `DEVNULL`을 써서 파이프/리더 스레드 자체를 만들지 말 것.** 서버가 "떠 있는데
응답이 없다"는 증상은 크래시가 아니라 **락/join 데드락**을 의심하고, `py-spy` 같은 도구로 실제
스레드 스택을 뜨는 게 로그만 보는 것보다 훨씬 빠르게 원인을 특정해준다.
관련: [[BUG-39]](관측 코드가 본작업을 죽임), [[BUG-42]](서브프로세스·격리 관련 완화).
