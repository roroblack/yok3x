# BUG-04 · CLI 백엔드 stdin 데드락 (무한 대기)

- **시점**: 2026-07-10 · **심각도**: 높음 · **커밋**: `44e5ee0` · **상태**: 수정됨 ✅

## 증상
producer-reviewer 런이 `running / step 0/0`에서 멈춰 진행되지 않음. run.log는
`[run] step 1 → claude-main (claude)` 이후 아무것도 없음. 스텝은 백엔드 응답 뒤에야
기록돼 `steps=[]`로 남고 GUI가 `step 0/0`으로 표시.

## 근본 원인
`backends._run_cli`의 `subprocess.run(..., capture_output=True)`이 **stdin을 리다이렉트하지
않았다**. 헤드리스(GUI 백그라운드 스레드) 실행 중 CLI가 인증·온보딩 등으로 대화형 입력을
기다리면, 상속된 stdin에서 입력을 못 받아 **`timeout_sec`(기본 600s)까지 데드락**.

## 진단
실제 스턱 런의 status.json/run.log 확인 → 첫 워커의 claude 서브프로세스에서 블로킹임을 특정.
GUI는 `auto=True`로 실행하므로 승인 게이트가 아니라 **백엔드 호출**에서 막힌 것.

## 수정
`subprocess.run(..., stdin=subprocess.DEVNULL)` — 자식이 즉시 EOF를 받아, 비대화형은 정상
진행하고 대화형이면 **빠르게 실패**(무한 대기 제거, RULE §5.5 명시적 열화). 프롬프트는 당시
argv(`{prompt}`)로 넘겼으므로 stdin 불필요. 겸사 `encoding=utf-8`로 Windows cp949 디코딩 방지.

## 검증
회귀 테스트: `subprocess.run`에 `stdin=DEVNULL` 전달·`{prompt}` 치환·utf-8 인코딩 단언.
실 서브프로세스에서 stdin 읽기 0.07초 완료(데드락 없음).

## 후속
이후 BUG-10에서 프롬프트를 stdin으로 넘기게 바뀌며, argv에 `{prompt}`가 없으면 `input=`으로
전달(EOF로 데드락 방지)하도록 발전. 이 수정이 그 토대가 됨.

## 교훈
헤드리스 subprocess는 **항상 stdin을 명시**(DEVNULL 또는 input)한다. 상속된 stdin은 대화형 대기로 데드락한다.
