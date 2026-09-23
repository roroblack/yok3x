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

/** `const NAME = {...}` 한 개를 소스 그대로 뽑는다(중괄호 균형 기준, extract()의 const 버전). */
function extractConst(name) {
  const start = html.indexOf(`const ${name}=`);
  assert.notEqual(start, -1, `gui/index.html에 const ${name}=가 없다`);
  let depth = 0, i = html.indexOf("{", start);
  const open = i;
  for (; i < html.length; i++) {
    if (html[i] === "{") depth++;
    else if (html[i] === "}") { depth--; if (depth === 0) break; }
  }
  assert.ok(i > open, `${name}: 중괄호 균형을 찾지 못함`);
  return html.slice(start, i + 1) + ";";
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

test("suggestBackendAccount(): 계정 이름과 인증 경로 기본값을 제안하고 중복을 피한다", () => {
  const { suggestBackendAccount } = load("suggestBackendAccount");
  assert.deepEqual(
    JSON.parse(JSON.stringify(suggestBackendAccount("codex", []))),
    { name: "codex-alt", authDir: "~/.codex-alt" });
  assert.deepEqual(
    JSON.parse(JSON.stringify(suggestBackendAccount("claude", ["claude-alt", "claude-alt2"]))),
    { name: "claude-alt3", authDir: "~/.claude-alt3" });
});

test("validateBackendAccountForm(): 필수값·이름·중복·gemini를 검증한다", () => {
  const { validateBackendAccountForm } = load("validateBackendAccountForm");
  assert.equal(validateBackendAccountForm("codex", "codex-alt", "~/.codex-alt", []), "");
  assert.match(validateBackendAccountForm("", "codex-alt", "x", []), /선택/);
  assert.match(validateBackendAccountForm("codex", "Bad Name", "x", []), /소문자/);
  assert.match(validateBackendAccountForm("codex", "codex-alt", "x", ["codex-alt"]), /이미 존재/);
  assert.equal(validateBackendAccountForm("codex", "codex-alt", "x", ["codex-alt"], "codex-alt"), "");
  assert.match(validateBackendAccountForm("gemini", "gemini-alt", "x", []), /격리/);
  assert.match(validateBackendAccountForm("claude", "claude-alt", "", []), /인증 디렉터리/);
});

test("validateBackendAccountForm(): API 키 모드는 디렉터리 대신 키를 요구한다", () => {
  const { validateBackendAccountForm } = load("validateBackendAccountForm");
  assert.match(
    validateBackendAccountForm("claude", "claude-api", "", [], "", "api_key", ""),
    /API 키/);
  assert.equal(
    validateBackendAccountForm("claude", "claude-api", "", [], "", "api_key", "sk-test"),
    "");
  // api_key 모드에서는 auth_dir가 비어 있어도 통과해야 한다(디렉터리 필수 체크를 건너뜀).
  assert.equal(
    validateBackendAccountForm("codex", "codex-api", "", [], "", "api_key", "sk-oai-test"),
    "");
  assert.match(
    validateBackendAccountForm("gemini", "gemini-api", "", [], "", "api_key", "sk-test"),
    /격리/);
  // authMode를 생략한 기존 호출(dir 모드)은 예전과 동일하게 동작해야 한다(회귀 없음).
  assert.equal(validateBackendAccountForm("codex", "codex-alt", "~/.codex-alt", []), "");
  assert.match(validateBackendAccountForm("claude", "claude-alt", "", []), /인증 디렉터리/);
});

const backendAccounts = [
  { name: "claude-alt2", account_of: "claude", family: "claude", auth_dir: "~/.claude-alt2" },
  { name: "codex", account_of: "", family: "codex", can_clone: true },
  { name: "claude", account_of: "", family: "claude", can_clone: true },
  { name: "gemini", account_of: "", family: "gemini", can_clone: false,
    clone_disabled_reason: "인증 디렉터리 격리 불가" },
  { name: "claude-alt", account_of: "claude", family: "claude", auth_dir: "~/.claude-alt" },
  { name: "codex-alt", account_of: "codex", family: "codex", auth_dir: "~/.codex-alt" },
];

test("groupBackendAccounts(): 계정군별로 원본 뒤에 복제를 원래 순서대로 묶는다", () => {
  const { groupBackendAccounts } = load("groupBackendAccounts");
  const groups = JSON.parse(JSON.stringify(groupBackendAccounts(backendAccounts)));
  assert.deepEqual(groups.map((group) => group.family), ["codex", "claude", "gemini"]);
  assert.deepEqual(groups.find((group) => group.family === "claude").accounts.map((a) => a.name),
    ["claude", "claude-alt2", "claude-alt"]);
  assert.deepEqual(groups.find((group) => group.family === "codex").accounts.map((a) => a.name),
    ["codex", "codex-alt"]);
});

test("buildBackendAccountCarousels(): 계정군마다 마지막 ⊕ 하나, 점 하나씩을 만든다", () => {
  const { groupBackendAccounts, applyBackendCardOrder, buildBackendAccountCarousels } = load(
    "groupBackendAccounts", "applyBackendCardOrder", "buildBackendAccountCarousels");
  const cards = JSON.parse(JSON.stringify(buildBackendAccountCarousels(backendAccounts, [])));
  for (const card of cards) {
    assert.equal(card.slides.filter((slide) => slide.kind === "add").length, 1);
    assert.equal(card.slides.at(-1).kind, "add");
    assert.equal(card.dots.length, card.slides.length);
    assert.equal(card.dots.at(-1).kind, "add");
  }
  const gemini = cards.find((card) => card.family === "gemini");
  assert.equal(gemini.slides.length, 2, "복제가 없어도 원본 + ⊕ 슬라이드");
  assert.equal(gemini.slides.at(-1).disabled, true);
  assert.match(gemini.slides.at(-1).reason, /격리 불가/);
});

test("mergeBackendCarouselScroll(): 폴링 재렌더에도 카드별 위치를 독립 보존한다", () => {
  const { mergeBackendCarouselScroll } = load("mergeBackendCarouselScroll");
  const first = JSON.parse(JSON.stringify(mergeBackendCarouselScroll(
    { claude: 120, codex: 40, gemini: 15 },
    [{ family: "claude", scrollLeft: 260, visible: true },
      { family: "gemini", scrollLeft: 0, visible: false }])));
  assert.deepEqual(first, { claude: 260, codex: 40, gemini: 15 });
  const second = JSON.parse(JSON.stringify(mergeBackendCarouselScroll(first,
    [{ family: "codex", scrollLeft: 180, visible: true }])));
  assert.deepEqual(second, { claude: 260, codex: 180, gemini: 15 });
});

test("applyBackendCardOrder(): 저장 순서를 적용하고 누락 계정군은 원래 순서로 뒤에 붙인다", () => {
  const { groupBackendAccounts, applyBackendCardOrder } = load("groupBackendAccounts", "applyBackendCardOrder");
  const groups = groupBackendAccounts(backendAccounts);
  const ordered = applyBackendCardOrder(groups, ["gemini", "unknown", "claude", "gemini"]);
  assert.deepEqual(Array.from(ordered, (group) => group.family), ["gemini", "claude", "codex"]);
});

test("paceTipText(): pace가 null이면 빈 문자열(BUG-44 회귀 방지 — 예전엔 render() 전체가 죽었음)", () => {
  const { paceTipText } = load("paceTipText");
  assert.equal(paceTipText(null, 50, false, null, 14, null, ""), "");
  assert.equal(paceTipText(undefined, 50, false, null, 14, null, ""), "");
});

test("paceTipText(): pace 있으면 소비율·상한을 담은 툴팁을 낸다", () => {
  const { paceTipText } = load("paceTipText");
  const tip = paceTipText({ used: 5.2 }, 20, false, null, 14, null, "");
  assert.ok(tip.includes("이번주 20%") && tip.includes("오늘 5.2%") && tip.includes("14%까지 여유"));
  const overTip = paceTipText({ used: 30 }, 40, true, 8.2, 14, null, "");
  assert.ok(overTip.includes("초과") && overTip.includes("8.2%"));
});

/** renderClaims는 document 없이도 순수 문자열을 반환한다 — esc/CLAIM_TYPE_LABEL/window만 있으면 됨. */
function loadRenderClaims() {
  const ctx = vm.createContext({ window: {} });
  vm.runInContext(
    [extract("esc"), extractConst("CLAIM_TYPE_LABEL"), extract("renderClaims")].join("\n"),
    ctx
  );
  return ctx.renderClaims;
}

test("renderClaims(): claim 없는 run은 빈 문자열(v4.6.0 S9)", () => {
  const renderClaims = loadRenderClaims();
  assert.equal(renderClaims({ run_id: "r1", understanding: null }), "");
  assert.equal(renderClaims({ run_id: "r1", understanding: { claims: [] } }), "");
});

test("renderClaims(): claim 텍스트·근거가 이스케이프돼 렌더된다 (BUG-19 계열 재확인)", () => {
  const renderClaims = loadRenderClaims();
  const html2 = renderClaims({
    run_id: "r1",
    understanding: {
      claims: [{
        claim_id: "c1", type: "OPEN_QUESTION",
        text: "<script>alert(1)</script>",
        evidence_refs: [{ file: "a.py", symbol_or_hunk: "<b>x</b>", source: "diff" }],
      }],
    },
  });
  assert.ok(!html2.includes("<script>alert(1)</script>"), "claim text가 이스케이프 안 됨");
  assert.ok(html2.includes("&lt;script&gt;"));
  assert.ok(html2.includes("QUESTION"));               // CLAIM_TYPE_LABEL 매핑
  assert.ok(html2.includes('data-claim="c1"'));
  assert.ok(html2.includes('data-action="explain"') && html2.includes('data-action="quiz"'));
});

test("renderClaims(): window._claimOpen/_claimResults 상태를 반영한다(폴링 재렌더 생존, 실측 확인된 버그)", () => {
  const ctx = vm.createContext({ window: {} });
  vm.runInContext(
    [extract("esc"), extractConst("CLAIM_TYPE_LABEL"), extract("renderClaims")].join("\n"),
    ctx
  );
  const claim = { claim_id: "c1", type: "FACT", text: "x", evidence_refs: [] };
  const run = { run_id: "r1", understanding: { claims: [claim] } };
  const key = "r1|c1";
  ctx.window._claimOpen = { [key]: true };
  ctx.window._claimResults = { [key]: '<div class="claim-result">답</div>' };
  const out = ctx.renderClaims(run);
  assert.ok(out.includes('class="claim open"'), "open 상태가 class에 반영 안 됨");
  assert.ok(out.includes('<div class="claim-result">답</div>'), "저장된 result가 안 보임");
});
