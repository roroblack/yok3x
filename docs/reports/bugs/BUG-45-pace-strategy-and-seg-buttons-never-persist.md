# BUG-45 · GUI 세그먼트 버튼(균등/유동/분산·경고만/정지+승인·plan) 클릭이 서버에 저장되지 않음

- **상태**: 수정 완료(2026-08-10) — codex 교차검증 완료
- **심각도**: **높음** — 설정 탭의 "상한 배분 방식"(`#pacestratseg`), "하루 페이싱 모드"
  (`#pacemodeseg`), "요금제"(`#planseg`) 세 세그먼트 전부, 버튼을 클릭해도 시각적으로만 눌린
  것처럼 보일 뿐 서버에는 전혀 반영되지 않았다. 사용자가 직접 재현("분산 눌러도 저장 안 되고
  다시 유동으로 돌아감", "유동 버튼 눌러도 다시 균등으로 돌아감")한 실사용 버그.
- **영역**: `gui/index.html`(클라이언트) + `yok3x/guiserver.py`의 `_apply_config`(서버) — **두
  개의 독립된 원인**이 겹쳐 있었다.
- **발견 경로**: 사용자가 "분산" 버튼을 눌러도 저장이 안 된다고 신고 → 1차 조사에서
  `_apply_config`의 `daily_pace.strategy` 화이트리스트에 `"spread"`가 빠진 것을 발견해 수정 →
  사용자가 "아직도 안 됨"이라고 재지적 → 실제 브라우저에서 `.click()`으로 재현하며 심층 조사한
  끝에 **완전히 별개의, 훨씬 큰 2차 원인**을 발견.

## 증상

1. 설정 탭에서 "분산" 버튼 클릭 → 버튼이 눌린 것처럼(`class="on"`) 보이지만 잠시 후(또는
   새로고침 시) "유동"으로 되돌아간다.
2. "유동" 버튼을 클릭해도 마찬가지로 저장되지 않고 "균등"으로 되돌아간다.
3. (조사 결과 추가 확인) "경고만/정지+승인"(`#pacemodeseg`)과 "요금제"(`#planseg`) 버튼도 동일
   증상 — 사용자가 명시적으로 요구한 전수 점검("설정에 있는 다른 옵션들도 바로 적용 안되는 거
   있나 체크해봐")에서 드러남.

## 근본 원인 ①: `_apply_config`의 낡은 화이트리스트 (경미, 부분 원인)

```python
if dp.get("strategy") in ("fixed", "catch_up"):     # spread 없음
    cur["strategy"] = dp["strategy"]
```

`spread`는 v3.6.0에서 정식 도입돼 `usage._pace_cfg`/`_daily_cap`은 이미 인식하지만, GUI 저장
API인 `_apply_config`의 이 화이트리스트만 갱신을 놓쳤다. `dp.get("strategy") in (...)`가
`False`가 되면 `cur["strategy"]`를 그냥 건드리지 않고 넘어가는데, 핸들러는 그래도
`{"ok": true}`를 반환한다 — **저장 실패를 성공처럼 보이게 하는 조용한 무시**였다. `"spread"`를
튜플에 추가해 수정.

이것만으로는 사용자가 재지적한 "아직도 안 됨" 증상을 설명하지 못했다 — 원인이 하나 더 있었다.

## 근본 원인 ②: `.seg` 전체에 적용되는 범용 코드가 `onclick` 속성 핸들러를 통째로 덮어씀 (핵심 원인)

`gui/index.html` 스크립트 맨 끝(로드 시 1회 실행):

```javascript
document.querySelectorAll(".seg").forEach(sg=>sg.querySelectorAll("button").forEach(b=>b.onclick=()=>{
  sg.querySelectorAll("button").forEach(x=>x.classList.remove("on"));b.classList.add("on");}));
```

`.seg` 클래스를 가진 컨테이너(`#c-pattern`, `#pacemodeseg`, `#pacestratseg`, `#planseg` 4개)의
**모든 버튼**에 대해 `.onclick` **프로퍼티**를 무조건 대입한다. 그런데 이 4개 중 3개
(`#pacemodeseg`/`#pacestratseg`/`#planseg`)의 버튼은 이미 HTML `onclick="savePace('warn')"` /
`onclick="savePaceStrategy('fixed')"` / `onclick="savePlan('pro')"` **속성**으로 실제 저장
함수를 갖고 있었다. JS에서 `element.onclick = fn`으로 대입하면 `onclick=` 속성에서 파생된
기존 핸들러를 **완전히 대체**한다 — 이 범용 코드가 실행되는 순간 세 세그먼트의 저장 함수
호출이 통째로 사라지고, 클릭 시 "시각적 `.on` 토글만 하는" 빈 함수로 치환됐다.

`#c-pattern`(작업 생성 폼의 producer-reviewer/pipeline 선택)만 애초에 `onclick` 속성이 없는
순수 시각적 선택 세그먼트였다 — 이 범용 코드는 원래 그것 하나만을 위해 만들어진 것으로
보인다. `git log -S`로 확인한 결과 이 코드는 `bb34742`(v3.0.1 리브랜드) 시점부터 있었고,
`savePace`/`savePaceStrategy`/`savePlan`이 `.seg` 컨테이너에 추가된 것은 그 이후 — **범용
코드가 나중에 추가된 기능과 상호작용을 검토받지 못한 채 방치된 잠복 버그**였다.

## 진단 과정(실측)

1. `_apply_config`의 화이트리스트를 고치고 서버 프로세스를 재시작한 뒤, 브라우저에서 raw
   `fetch("/api/config", {body:{daily_pace:{strategy:"fixed"}}})`를 직접 호출 → 정상 저장 확인.
   여기서 "고쳐졌다"고 1차 판단했으나 성급했다.
2. 실제 GUI 버튼을 `javascript_tool`로 진짜 DOM `.click()` 디스패치(좌표 기반 `computer` 클릭은
   이 환경에서 신뢰 불가 — Browser pane 미표시 시 조용히 실패할 수 있음이 별도로 확인됨)해
   재현 → 버튼 class는 `"on"`으로 바뀌지만 서버 값(`GET /api/state`)은 그대로.
3. `window.fetch`를 감싸 호출 여부를 기록 → **클릭 시 `/api/config`로의 POST가 아예 발생하지
   않음**(2초 대기해도 0회). 반면 `window.savePaceStrategy('fixed')`를 직접 호출하면 정상적으로
   `fetch`가 발생하고 저장됨 — 같은 함수인데 호출 경로에 따라 동작이 다르다는 모순 확인.
4. 버튼 요소의 `.onclick` 프로퍼티를 `.toString()`으로 직접 출력 → `onclick="savePaceStrategy(...)"`
   속성과 무관한, 시각적 토글만 하는 별개의 익명 함수였음을 확인. `grep`으로 그 함수의 출처를
   찾아 원인 ②를 특정.
5. 같은 패턴이 `#pacemodeseg`/`#planseg`에도 적용되는지 실측 재현으로 확인(둘 다 재현됨).

## 수정

1. (`yok3x/guiserver.py`) `_apply_config`의 `daily_pace.strategy` 화이트리스트에 `"spread"` 추가.
2. (`gui/index.html`) 범용 `.seg` 오버라이드 코드를 제거하고 `#c-pattern` 전용으로 명시:
   ```javascript
   document.querySelectorAll("#c-pattern button").forEach(b=>b.onclick=()=>{
     document.querySelectorAll("#c-pattern button").forEach(x=>x.classList.remove("on"));
     b.classList.add("on");});
   ```
   (1차 시도는 `if(b.hasAttribute("onclick")) return;`로 조건부 스킵하는 방식이었으나, **codex
   교차검증**에서 "향후 다른 `.seg` 버튼이 `onclick` 속성 없이 JS로만 핸들러를 붙이면 이 조건이
   다시 깨질 수 있다"는 지적을 받아 대상을 `#c-pattern`으로 명시하는 더 안전한 방식으로 교체.)

## 검증

- 라이브 브라우저에서 실제 `.click()` 디스패치로 4개 세그먼트 전부 재확인:
  - `#pacestratseg`: spread 클릭 → 서버 `strategy=spread` 확인, catch_up 클릭 → 원복 확인.
  - `#pacemodeseg`: pause 클릭 → 서버 `mode=pause` 확인, warn 클릭 → 원복 확인.
  - `#planseg`: pro 클릭 → 서버 `plan=pro` 확인, 원래 값(max5x)으로 원복 확인.
  - `#c-pattern`: 클릭 시 여전히 순수 시각적으로 `.on` 토글(서버 호출 없음, 회귀 없음) 확인.
- **codex(`codex exec`) 교차검증**: 진단·근본 원인·수정의 타당성 확인 + 더 견고한 대안(대상
  명시) 제안 → 반영.
- 회귀 테스트: `_apply_config`가 `daily_pace.strategy`의 세 값(`fixed`/`catch_up`/`spread`)을
  모두 받아들이는지, 알 수 없는 값은 무시하는지 검증하는 Python 테스트 2개 추가
  (`test_apply_config_accepts_all_three_daily_pace_strategies`,
  `test_apply_config_rejects_unknown_daily_pace_strategy_silently`).
- 전체 스위트: Python 426 passed / 1 skipped, JS 9 passed — 회귀 없음.

## 교훈

- **"버튼이 시각적으로 눌렸다" ≠ "저장됐다"** — `element.onclick = fn` 대입은 `onclick=` HTML
  속성에서 파생된 핸들러를 조용히, 완전히 대체한다. 같은 요소에 대해 속성 기반 핸들러와 JS
  대입 핸들러가 공존할 수 없다는 것을 잊고 "이미 붙어 있으니 괜찮겠지"라고 가정하면 이런
  버그가 생긴다.
- **좌표 기반 합성 클릭(`computer` 도구)보다 진짜 DOM `.click()` 디스패치(`javascript_tool`)가
  이 종류의 버그를 재현하는 데 훨씬 신뢰할 수 있다** — 전자는 Browser pane이 실제로
  compositing 중이 아니면 조용히 실패할 수 있어, "버튼이 안 눌렸다"와 "눌렸는데 핸들러가
  잘못됐다"를 구분하지 못한다.
- **"고쳐졌다"고 보고하기 전에 실제 클릭 경로로 끝까지 재현해야 한다** — 1차 수정(화이트리스트)
  후 raw `fetch()` 직접 호출로만 검증하고 "해결"이라 보고했다가 사용자에게 정정당했다. raw
  fetch는 서버 로직만 검증할 뿐, 클라이언트의 실제 클릭 경로(이벤트 핸들러 배선)는 전혀
  검증하지 못한다 — 둘은 별개의 실패 지점이다.
- **하나의 증상 신고 뒤에 여러 개의 독립된 원인이 겹쳐 있을 수 있다** — 화이트리스트 수정만으로
  "고쳤다"고 판단했지만, 실제로는 훨씬 큰 별개의 버그가 같은 증상을 내고 있었다. 사용자가
  "다른 옵션들도 체크해봐"라고 전수 점검을 요구한 덕분에 `#pacemodeseg`/`#planseg`까지 같은
  근본 원인의 피해자임을 확인할 수 있었다 — 단일 증상 재현만으로 "범위를 다 파악했다"고
  가정하지 말 것.
- **범용/공통 코드는 새 기능이 그 대상 범위에 편입될 때마다 재검토가 필요하다** — `.seg` 오버
  라이드는 `#c-pattern` 하나만을 염두에 두고 작성됐지만, 이후 `#pacemodeseg` 등이 같은 CSS
  클래스(`.seg`)를 재사용하면서 원래 의도와 무관하게 피해자가 됐다. CSS 클래스 재사용이
  "스타일만 공유"가 아니라 "동작까지 공유"로 이어질 수 있음을 놓치기 쉽다.
