# BUG-11 · GUI JS 식별자 충돌 (스크립트 전체 무력화)

- **시점**: 2026-07-11 · **심각도**: 높음 · **커밋**: `06ed52a` · **상태**: 수정됨 ✅

## 증상
S3 GUI(프로파일 선택기) 추가 후, 프로파일 드롭다운·라우팅 미리보기가 **아예 렌더 안 됨**.
브라우저에서 `render`·`load`·`opts` 등 **모든 함수가 undefined**, 콘솔 에러는 안 보임.

## 근본 원인 (두 겹)
1. **JS 식별자 재선언**: `renderConsole`에 이미 `const rt`(= c-regtask)가 있는데, 새 라우팅
   미리보기 코드가 또 `const rt`(= c-routing)를 선언 → `SyntaxError: Identifier 'rt' has
   already been declared`. 스크립트가 **파싱 단계에서 통째로 실패**해 함수가 하나도 정의 안 됨.
2. **상태 키 충돌**: `/api/state`에 추가한 `routing`(라우팅 미리보기)이 기존 콘솔 보드용
   `routing`(작업→backend 맵)과 같은 키라, dict에서 나중 값이 덮어써 미리보기가 사라짐.

## 진단
페이지에서 `typeof render` 등이 전부 `undefined`임을 확인 → 스크립트 파싱 실패 의심.
`<script>` 블록을 추출해 **`node --check`** → 정확한 라인·에러(`rt` 재선언) 특정.
`/api/state` raw 덤프로 `routing`이 보드 맵임을 확인(키 충돌).

## 수정
- 변수 `rt` → `rtp`로 개명(재선언 해소).
- 상태 키 `routing` → `route_preview`로 개명(보드 `routing`과 분리), GUI JS도 맞춤.

## 검증
추출 스크립트 `node --check` → OK. 브라우저 리로드 후 `typeof load === 'function'`,
프로파일 옵션·라우팅 미리보기 렌더 확인.

## 교훈
- 인라인 `<script>`의 단일 문법 오류는 **스크립트 전체를 무력화**한다(부분 실패 아님).
  GUI JS 편집 후 **`node --check`로 문법 검증**을 습관화.
- 상태/DOM에 키·변수를 추가할 땐 **기존 이름과의 충돌**을 먼저 확인.
