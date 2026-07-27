// F2-8: GUI 순수 함수 테스트 — **의존성 0**(Node 내장 `node:test`/`node:assert`/`node:vm`만).
//
// 설계 의도: `gui/index.html`은 인라인 <script> 한 덩어리다. 테스트하려고 파일을 쪼개면 GUI를
// 건드리게 되므로(RULE §5.6: 명령 없이 GUI 수정 금지), **원본을 읽어 함수 소스만 추출해**
// 샌드박스에서 평가한다. GUI는 한 글자도 바뀌지 않는다.
//
// 실행: `node --test tests/js/`   (pytest에서도 래퍼로 함께 돌아간다)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "..", "gui", "index.html"), "utf8");

/** index.html에서 `function NAME(...){...}` 한 개를 소스 그대로 뽑는다(중괄호 균형 기준). */
function extract(name) {
  const start = html.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `gui/index.html에 function ${name}(가 없다`);
  let depth = 0, i = html.indexOf("{", start);
  const open = i;
  for (; i < html.length; i++) {
    if (html[i] === "{") depth++;
    else if (html[i] === "}") { depth--; if (depth === 0) break; }
  }
  assert.ok(i > open, `${name}: 중괄호 균형을 찾지 못함`);
  return html.slice(start, i + 1);
}

/** 뽑은 함수들을 격리 컨텍스트에서 평가해 돌려준다. DOM·window 없이 동작하는 순수 함수만 대상. */
function load(...names) {
  const ctx = vm.createContext({});
  vm.runInContext(names.map(extract).join("\n"), ctx);
  return Object.fromEntries(names.map((n) => [n, ctx[n]]));
}

test("esc(): HTML 특수문자를 모두 이스케이프 (BUG-19 회귀)", () => {
  const { esc } = load("esc");
  // BUG-19: 에이전트 산출물의 <button>/<div>가 이스케이프 없이 innerHTML에 들어가 DOM이 붕괴했다.
  assert.equal(esc('<button onclick="x">'), "&lt;button onclick=&quot;x&quot;&gt;");
  assert.equal(esc("a & b"), "a &amp; b");
  assert.equal(esc("it's"), "it&#39;s");
  assert.equal(esc("<script>alert(1)</script>"),
    "&lt;script&gt;alert(1)&lt;/script&gt;");
  // null/undefined는 빈 문자열 — "null"이 화면에 찍히면 안 된다
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(0), "0");            // 0은 살아야 한다(falsy 함정)
});

test("fclsPct(): 창별 임계 분류가 guard 비율을 따른다 (BUG-20 계열)", () => {
  const { fclsPct } = load("fclsPct");
  const g = { soft: 0.8, hard: 1.0 };
  assert.equal(fclsPct(50, g), "f-ok");
  assert.equal(fclsPct(80, g), "f-warn");     // soft 도달 = 경계 포함
  assert.equal(fclsPct(99.9, g), "f-warn");
  assert.equal(fclsPct(100, g), "f-stop");    // hard 도달
  assert.equal(fclsPct(150, g), "f-stop");
  // guard 없으면 기본값(0.8/1.0)으로 동작해야 한다 — 설정 누락이 무등급으로 새면 안 됨
  assert.equal(fclsPct(85, null), "f-warn");
  assert.equal(fclsPct(10, undefined), "f-ok");
});

test("wlvl(): 창 경고 레벨은 미달 시 빈 문자열", () => {
  const { wlvl } = load("wlvl");
  const g = { soft: 0.8, hard: 1.0 };
  assert.equal(wlvl(10, g), "");
  assert.equal(wlvl(80, g), "warn");
  assert.equal(wlvl(100, g), "stop");
});

test("fmtTok()/fmtDur(): 사람이 읽는 축약 — 측정불가는 —", () => {
  const { fmtTok, fmtDur } = load("fmtTok", "fmtDur");
  assert.equal(fmtTok(999), 999);
  assert.equal(fmtTok(1500), "2K");
  assert.equal(fmtTok(2_500_000), "2.5M");
  assert.equal(fmtDur(null), "—");            // 0과 '측정 불가'를 구분(A-lite 원칙)
  assert.equal(fmtDur(1500), "1.5s");
  assert.equal(fmtDur(125_000), "2m5s");
});
