# BUG-44 · `paceTip` 계산이 `t.pace===null`을 안 가려 `render()` 전체가 죽음 (GUI "서버 연결 끊김" 오탐)

- **상태**: 수정 완료(2026-08-10)
- **심각도**: **중간** — 실제로는 서버가 멀쩡하고 `/api/state`가 200을 내는데, GUI가 "서버 연결
  끊김"으로 오탐해 대시보드 전체가 멈춘 것처럼 보임(사용자가 실제 장애로 오인하기 쉬움).
- **영역**: `gui/index.html`의 `render()` 내부 `s.tools.map(...)` — daily pace 툴팁 조립.
- **발견 경로**: v4.6.0 S9(Cognitive Sync Layer 클릭형 퀴즈/설명) GUI 구현을 라이브 브라우저로
  검증하던 중 우연히 발견. 내가 만든 기능과는 무관한 **기존(2026-07-24부터 있던) 잠복 버그**.

## 증상

`mock` 백엔드로 스캐폴드한 프로젝트에서 GUI를 열면, `/api/state`가 200 OK를 내는데도 화면에
"서버 연결 끊김 — /api/state 응답 없음(연속 1회, 약 7초)"가 표시되고 대시보드 본문이 비어 있었다.
네트워크 탭에는 실패한 요청이 하나도 없었다.

## 근본 원인

`load()`의 성공 경로는 `render(s)`를 같은 `try` 블록 안에서 호출한다:

```javascript
try{const r=await fetch("/api/state",{cache:"no-store"});if(!r.ok)throw 0;
  const s=await r.json();everLive=true;fails=0;
  ...
  setLive(true);render(s);}
catch(e){ fails++; ...
  el.style.display="block";   // "서버 연결 끊김" 배너
  setLive(false);
}
```

`render(s)`가 던지는 예외도 이 `catch`가 그대로 삼켜 **"연결 끊김"으로 오분류**한다 — fetch는
성공했는데 **렌더링 단계의 버그**를 네트워크 장애로 착각하게 만드는 구조다.

`render()` 안에서 각 도구(tool)의 각 창(window)마다 pace 툴팁을 조립하는데:

```javascript
const paceTip=`이번주 ${w.used_percent}% 소비 · 오늘 ${t.pace.used}% 소비` + ...
```

`t.pace`는 `guiserver.py`의 `build_state()`에서 **`v.reading and v.real`일 때만** 채워지고, 그 외
(측정 실패·`mock` 백엔드처럼 라이브 리딩이 없는 backend 등)는 `None`으로 내려온다. 몇 줄 뒤의
실제 사용처(`pl=...`)는 `isWk&&t.pace`로 정확히 가드하지만, **`paceTip` 자체의 계산은 그 가드보다
먼저, 무조건 `t.pace.used`를 참조**한다 — `t.pace`가 `null`인 도구/창을 만나는 순간
`TypeError: Cannot read properties of null (reading 'used')`가 던져지고, `render()` 전체가
중단된다(그 뒤에 그려질 예정이던 콘솔 로그·claim 영역 등도 전부 안 그려짐).

## 진단

1. 실제 HTTP 응답을 파일로 저장해 바이트 단위로 확인 — 서버가 보낸 JSON은 완전히 정상(순수
   UTF-8)이었다(터미널에 직접 출력해서 봤을 때 보였던 깨진 문자는 **내 진단 터미널의 cp949 콘솔
   출력 문제**였을 뿐, 실제 데이터·GUI 렌더링과는 무관 — 별도 확인, 코드 수정 없음).
2. `read_network_requests`로 확인 — 모든 `/api/state` 요청이 200 OK.
3. 브라우저에서 `fetch("/api/state")`→`render(s)`를 직접 실행해 실제 예외를 잡음:
   `TypeError: Cannot read properties of null (reading 'used')` at `render()` 내부 `paceTip` 줄.
4. `git blame`으로 해당 줄이 2026-07-24 커밋(`46c5bcd`, 오늘 세션과 무관)부터 있었음을 확인 —
   pre-existing 버그.

## 수정

1. `paceTip` 계산 전체를 `t.pace ? ... : ""`로 감싸 `t.pace`가 없으면 즉시 빈 문자열을 반환하게 함.
2. 재발 방지를 위해 이 계산을 별도 순수 함수 `paceTipText(pace, usedPercent, over, fd, cap, ec, tok)`
   로 추출(F2-8 순수함수 테스트 하네스로 단위 테스트 가능하게). `render()`는 이제
   `paceTipText(t.pace, w.used_percent, over, fd, cap, ec, tok)`만 호출한다.

## 검증

- 라이브 브라우저(mock 백엔드, gemini 등 `pace=null`인 도구 포함)로 재현 → 수정 후 "● LIVE"로
  정상 로드, "서버 연결 끊김" 오탐 사라짐 확인(스크린샷 비교).
- 신규 JS 테스트 2개(`paceTipText`): `pace=null/undefined`→빈 문자열, `pace` 있으면 정상 툴팁.
- 전체 스위트(Python 424 + JS 9) 통과.

## 교훈

- **`try{...; render(s)}catch(e){설정: 연결 끊김}` 구조는 "fetch 실패"와 "렌더 버그"를 구분하지
  못한다** — 사용자에게는 둘 다 "서버가 죽었다"로 보이지만 원인·대응이 완전히 다르다(BUG-43의
  "무응답=크래시로 착각 말고 락/join을 의심하라"는 교훈과 대칭— 이번엔 "무응답 배너=네트워크로
  착각 말고 렌더 예외를 의심하라"). 근본적으로는 `render()`의 예외를 fetch 실패와 별도로
  분류해 보여주는 게 맞지만(예: "데이터는 받았는데 표시 실패"), 이번 수정 범위 밖(후속 후보).
- **가드가 있는 코드 근처에 가드 없는 같은 값 참조가 있으면 의심할 것** — `pl`은 `isWk&&t.pace`로
  정확히 가드했는데, 바로 위 `paceTip`은 그 가드를 안 썼다. "이 값 근처에 이미 null 체크가 있다"는
  안심의 함정.
- 진단 중 겪은 사이드 이슈: **이 환경의 bash/PowerShell 콘솔이 cp949라 한글 UTF-8 텍스트를 화면에
  출력하면 깨져 보인다** — 실제 파일·HTTP 응답이 깨졌는지는 항상 **파일로 저장해 바이트 단위로**
  확인해야 한다(터미널 출력만 보고 "데이터가 깨졌다"고 결론 내리면 오진).
