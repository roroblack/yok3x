# BUG-19 · 콘솔 채팅이 세로→가로로 붕괴(에이전트 산출물 HTML이 DOM 파괴)

- **상태**: 수정 완료 (2026-07-16)
- **심각도**: 높음(콘솔 사용 불가 수준의 레이아웃 붕괴) · 부가적으로 DOM 인젝션(저장형) 취약
- **영역**: `gui/index.html` — `renderConsoleLog()` / 대시보드 runs 렌더 / 콘솔 필터

## 증상
콘솔 탭에서 채팅 메시지가 **세로로 쌓이지 않고 가로로 나열**되고, 글자가 한 글자씩
세로로 줄바꿈되는 형태로 깨져 보임. "작업 비율"(사용량 바 등 콘솔 우측)도 이상하게 보임.
특정 프로젝트 상태에선 멀쩡했는데 어느 순간부터 재현.

## 근본 원인 (2겹)
1. **[주원인] 이스케이프 안 된 innerHTML DOM 파괴**: `renderConsoleLog`가 에이전트 스텝의
   `summary`·`task`·`issues` 등을 **이스케이프 없이 `innerHTML`에 삽입**. 에이전트 산출물에
   `<button>`·`<div>` 등 태그가 포함되면(예: **"간단한 계산기" 작업이 생성한 단일 HTML 파일**
   코드가 summary에 노출) 브라우저가 그 태그를 실제 마크업으로 해석 → 이후 `.you`/`.cstep`
   요소들이 서로 **중첩**되며 구조가 붕괴. → "전전 상태에선 멀쩡" = 그때는 `<`/`>` 포함
   summary 런이 없었기 때문. (라이브 확인: 현재 런 summary 5건에 `<`/`>` 포함)
2. **[증폭] 그리드 blowout**: `.cols{grid-template-columns:1.5fr 1fr}`가 `minmax(0,..)`이 없어,
   붕괴로 생긴 넓은 min-content가 `fr` 트랙을 뷰포트 밖(2352px)으로 밀어 콘솔 전체가 가로 폭발.
   `.cstep .cb`도 `min-width:0`이 없어 flex 아이템이 콘텐츠에 안 줄어듦.

## 수정
- **핵심**: `esc()` HTML 이스케이프 헬퍼 추가 → 콘솔/대시보드 런 렌더·필터의 **모든 동적 텍스트**
  (run_id·pattern·state·worker·kind·status·score·issues·summary·label·task)에 적용. DOM 파괴 원천 차단.
- **방어**: `.cols`를 `minmax(0,1.5fr) minmax(0,1fr)`로, `.cstep .cb{min-width:0}`,
  `.you .b/.cstep .cb/.av/.meta{overflow-wrap:anywhere}` — 긴 토큰(run_id 등)도 안전하게 줄바꿈.

## 검증
- 재현: 문제 cstep 내부에 다른 `.cstep`/`.you`가 중첩(DOM 파괴)됨을 확인, 콘솔 폭 2308px.
- 수정 후: 중첩 0, 오버플로 요소 0, `#console-log` scrollW==clientW(496), 자식 전부 left=54(세로 적재),
  파란 사용자 말풍선+에이전트 스텝 카드가 세로로 정상 렌더. JS 에러 없음. 74 tests 영향 없음(GUI-only).

## 관련
- BUG-18(계산기 실패)의 후속: 계산기 작업이 정상화되어 **HTML 산출물이 콘솔에 흘러든** 것이 방아쇠.
- RULE §5.6: UI 변경이나 전부 사용자 요청("ui/ux 이상해졌어, 고쳐")에 따른 것.
