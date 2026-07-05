import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState, useEffect } from "react";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

type ToolKey = "claude" | "codex" | "gemini";
type Tab = "dashboard" | "runs" | "tasks" | "settings" | "knot";

type Tool = {
  key: ToolKey;
  name: string;
  plan: string;
  source: "실측" | "추정" | "원장";
  usage5h: number;
  usage7d: number;
  reset5h: string;
  reset7d: string;
  calls: number;
  tokens: string;
  cost: number;
  color: string;
  emoji: string;
};

const TOOLS: Tool[] = [
  { key: "claude", name: "Claude", plan: "max20", source: "추정", usage5h: 42, usage7d: 61, reset5h: "2h 14m", reset7d: "4d 03h", calls: 118, tokens: "1.2M", cost: 8.42, color: "var(--snoopy-blue)", emoji: "🦴" },
  { key: "codex",  name: "Codex",  plan: "plus",  source: "실측", usage5h: 78, usage7d: 84, reset5h: "0h 47m", reset7d: "2d 11h", calls: 231, tokens: "2.7M", cost: 14.10, color: "var(--snoopy-yellow)", emoji: "✏️" },
  { key: "gemini", name: "Gemini", plan: "pro",   source: "원장", usage5h: 22, usage7d: 35, reset5h: "3h 02m", reset7d: "5d 18h", calls: 64,  tokens: "540K", cost: 2.31, color: "var(--snoopy-pink)", emoji: "🌟" },
];

const SOFT = 80;
const HARD = 100;

function statusOf(pct: number) {
  if (pct >= HARD) return { label: "정지", color: "var(--snoopy-red)", text: "text-white" };
  if (pct >= SOFT) return { label: "경고", color: "var(--snoopy-yellow)", text: "text-[var(--ink)]" };
  return { label: "여유", color: "var(--snoopy-green)", text: "text-[var(--ink)]" };
}

const SAMPLE_RUNS = [
  { id: "run_8f2a", pattern: "producer-reviewer", state: "running" as const, step: 3, total: 5, worker: "codex-critic", score: 82 },
  { id: "run_8e91", pattern: "pipeline",          state: "done" as const,    step: 4, total: 4, worker: "gemini",       score: 94 },
  { id: "run_8dc0", pattern: "fanout-fanin",      state: "stopped_by_guard" as const, step: 2, total: 6, worker: "claude-main", score: 71 },
  { id: "run_8d55", pattern: "producer-reviewer", state: "done" as const,    step: 5, total: 5, worker: "claude-main", score: 88 },
];

const SAMPLE_STEPS = [
  { i: 1, worker: "claude-main",  backend: "cli",    status: "done", score: 74, issues: 2 },
  { i: 2, worker: "codex-critic", backend: "native", status: "done", score: 79, issues: 1 },
  { i: 3, worker: "claude-main",  backend: "cli",    status: "done", score: 82, issues: 0 },
  { i: 4, worker: "codex-critic", backend: "native", status: "running", score: null, issues: 0 },
  { i: 5, worker: "claude-main",  backend: "cli",    status: "pending", score: null, issues: 0 },
];

function Dashboard() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [guardOn, setGuardOn] = useState(true);
  const [flavor, setFlavor] = useState("claude");
  const [soft, setSoft] = useState(SOFT);
  const [hard, setHard] = useState(HARD);
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen">
      <TopBar
        flavor={flavor} setFlavor={setFlavor}
        guardOn={guardOn} setGuardOn={setGuardOn}
        soft={soft} hard={hard} now={now}
      />
      <TabBar tab={tab} setTab={setTab} />

      <main className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 pb-24">
        {tab === "dashboard" && <DashboardView />}
        {tab === "runs" && <RunsView />}
        {tab === "tasks" && <TasksView />}
        {tab === "settings" && <SettingsView soft={soft} setSoft={setSoft} hard={hard} setHard={setHard} guardOn={guardOn} setGuardOn={setGuardOn} />}
        {tab === "knot" && <KnotView />}
      </main>

      <Footer />
    </div>
  );
}

/* ---------------- Top bar ---------------- */

function TopBar({
  flavor, setFlavor, guardOn, setGuardOn, soft, hard, now,
}: {
  flavor: string; setFlavor: (v: string) => void;
  guardOn: boolean; setGuardOn: (v: boolean) => void;
  soft: number; hard: number; now: Date;
}) {
  return (
    <header className="border-b-[2.5px] border-[var(--ink)] bg-[var(--card)]">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-4 flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-full bg-white comic-border grid place-items-center comic-shadow-sm wiggle">
            <span className="text-2xl">🐶</span>
          </div>
          <div>
            <h1 className="text-2xl font-extrabold leading-none">
              HARNESS
              <span className="ml-2 hand text-[var(--snoopy-red)] text-xl">good grief!</span>
            </h1>
            <p className="text-xs text-[var(--muted-foreground)] mt-0.5">멀티 에이전트 오케스트레이터 · flavor: <span className="font-bold">{flavor}</span></p>
          </div>
        </div>

        <div className="flex-1" />

        <label className="flex items-center gap-2 sticker px-3 py-2 cursor-pointer">
          <span className="text-sm font-bold">가드</span>
          <button
            onClick={() => setGuardOn(!guardOn)}
            aria-label="Toggle guard"
            className={`relative w-12 h-6 rounded-full comic-border transition-colors ${guardOn ? "bg-[var(--snoopy-green)]" : "bg-[var(--paper-dark)]"}`}
          >
            <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white comic-border transition-all ${guardOn ? "left-6" : "left-0.5"}`} />
          </button>
          <span className={`text-xs font-bold ${guardOn ? "text-[var(--snoopy-green)]" : "text-[var(--muted-foreground)]"}`}>
            {guardOn ? "ON" : "OFF"}
          </span>
        </label>

        <div className="sticker px-3 py-2 text-xs">
          <span className="hand text-base mr-2">임계</span>
          <span className="font-bold text-[var(--snoopy-yellow)]">warn {soft}%</span>
          <span className="mx-1 text-[var(--muted-foreground)]">·</span>
          <span className="font-bold text-[var(--snoopy-red)]">stop {hard}%</span>
        </div>

        <select
          value={flavor}
          onChange={(e) => setFlavor(e.target.value)}
          className="sticker px-3 py-2 text-sm font-bold bg-[var(--snoopy-yellow)] cursor-pointer"
        >
          <option value="claude">🦴 claude</option>
          <option value="codex">✏️ codex</option>
          <option value="gemini-orchestrator">🌟 gemini</option>
        </select>

        <button className="sticker px-3 py-2 text-sm font-bold bg-[var(--snoopy-pink)] hover:bounce-soft">
          ↻ 한도
        </button>

        <div className="sticker px-3 py-2 text-sm font-mono tabular-nums">
          {now.toLocaleTimeString("ko-KR", { hour12: false })}
        </div>
      </div>
    </header>
  );
}

/* ---------------- Tabs ---------------- */

function TabBar({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const tabs: { key: Tab; label: string; emoji: string }[] = [
    { key: "dashboard", label: "대시보드", emoji: "🏠" },
    { key: "runs",      label: "런",       emoji: "▶️" },
    { key: "tasks",     label: "태스크",   emoji: "📝" },
    { key: "settings",  label: "설정",     emoji: "⚙️" },
    { key: "knot",      label: "knot",     emoji: "🧶" },
  ];
  return (
    <nav className="border-b-[2.5px] border-[var(--ink)] bg-[var(--paper-dark)] sticky top-0 z-40">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 flex gap-1 overflow-x-auto">
        {tabs.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-3 text-sm font-bold whitespace-nowrap transition-all border-x-[2.5px] border-t-[2.5px] border-transparent -mb-[2.5px] ${
                active
                  ? "bg-[var(--card)] border-[var(--ink)] rounded-t-lg relative"
                  : "text-[var(--muted-foreground)] hover:text-[var(--ink)]"
              }`}
            >
              <span className="mr-1.5">{t.emoji}</span>{t.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

/* ---------------- Dashboard view ---------------- */

function DashboardView() {
  return (
    <div className="pt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <section className="lg:col-span-2 space-y-6">
        <SectionTitle emoji="📊" title="도구 한도" sub="claude · codex · gemini" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {TOOLS.map((t) => <ToolCard key={t.key} tool={t} />)}
        </div>

        <SectionTitle emoji="▶️" title="최근 런" sub="run_id · pattern · progress" />
        <RunsList compact />
      </section>

      <aside className="space-y-6">
        <SectionTitle emoji="💭" title="코치" sub="어디로 옮길까?" />
        <CoachPanel />
        <QuickStats />
      </aside>
    </div>
  );
}

function SectionTitle({ emoji, title, sub }: { emoji: string; title: string; sub: string }) {
  return (
    <div className="flex items-end gap-3">
      <span className="text-2xl">{emoji}</span>
      <h2 className="text-2xl font-extrabold">{title}</h2>
      <span className="hand text-[var(--muted-foreground)] pb-1">{sub}</span>
    </div>
  );
}

/* ---------------- Tool card ---------------- */

function ToolCard({ tool }: { tool: Tool }) {
  const worst = Math.max(tool.usage5h, tool.usage7d);
  const st = statusOf(worst);

  return (
    <article className="sticker p-4 relative overflow-hidden">
      <div
        className="absolute -top-8 -right-8 w-24 h-24 rounded-full opacity-40"
        style={{ background: tool.color }}
      />
      <div className="relative flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-11 h-11 rounded-full comic-border grid place-items-center text-xl" style={{ background: tool.color }}>
            {tool.emoji}
          </div>
          <div>
            <h3 className="font-extrabold text-lg leading-none">{tool.name}</h3>
            <p className="text-xs text-[var(--muted-foreground)] mt-1">plan: <span className="font-bold">{tool.plan}</span></p>
          </div>
        </div>
        <span
          className={`px-2 py-1 rounded-full text-xs font-bold comic-border ${st.text}`}
          style={{ background: st.color }}
        >
          {st.label}
        </span>
      </div>

      <Gauge label="5시간" pct={tool.usage5h} reset={tool.reset5h} />
      <div className="h-3" />
      <Gauge label="7일"   pct={tool.usage7d} reset={tool.reset7d} />

      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        <SourceBadge kind={tool.source} />
        <span className="text-[10px] text-[var(--muted-foreground)]">·</span>
        <span className="text-xs font-bold">{tool.calls} calls</span>
        <span className="text-[10px] text-[var(--muted-foreground)]">·</span>
        <span className="text-xs font-bold">{tool.tokens}</span>
        <span className="text-[10px] text-[var(--muted-foreground)]">·</span>
        <span className="text-xs font-bold text-[var(--snoopy-green)]">${tool.cost.toFixed(2)}</span>
      </div>
    </article>
  );
}

function Gauge({ label, pct, reset }: { label: string; pct: number; reset: string }) {
  const st = statusOf(pct);
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs mb-1">
        <span className="font-bold">{label}</span>
        <span className="tabular-nums">
          <span className="font-extrabold text-base">{pct}%</span>
          <span className="text-[var(--muted-foreground)] ml-2 hand">리셋 {reset}</span>
        </span>
      </div>
      <div className="h-4 comic-border rounded-full bg-white overflow-hidden relative">
        <div
          className="h-full transition-all"
          style={{ width: `${Math.min(pct, 100)}%`, background: st.color }}
        />
        <div
          className="absolute top-0 bottom-0 w-[1.5px] bg-[var(--ink)]/40"
          style={{ left: `${SOFT}%` }}
          title={`soft ${SOFT}%`}
        />
      </div>
    </div>
  );
}

function SourceBadge({ kind }: { kind: Tool["source"] }) {
  const map = {
    "실측": { c: "var(--snoopy-green)", t: "text-white" },
    "추정": { c: "var(--snoopy-yellow)", t: "text-[var(--ink)]" },
    "원장": { c: "var(--snoopy-blue)", t: "text-white" },
  } as const;
  const s = map[kind];
  return (
    <span className={`px-1.5 py-0.5 rounded comic-border text-[10px] font-extrabold ${s.t}`} style={{ background: s.c }}>
      {kind}
    </span>
  );
}

/* ---------------- Coach panel ---------------- */

function CoachPanel() {
  const cards = TOOLS.map((t) => {
    const worst = Math.max(t.usage5h, t.usage7d);
    const st = statusOf(worst);
    const suggest =
      worst >= HARD ? "→ 다른 도구로 옮겨" :
      worst >= SOFT ? `→ ${TOOLS.find(x => x.key !== t.key && Math.max(x.usage5h, x.usage7d) < SOFT)?.name ?? "여유 있는 도구"}로 옮기는 게 좋아` :
      "여기서 계속 굴려도 좋아";
    return { tool: t, st, worst, suggest };
  });

  return (
    <div className="space-y-3">
      <div className="sticker bg-white p-4 bubble-tail wiggle">
        <p className="hand text-lg leading-snug">
          "codex가 헐떡이고 있어요.  <br />
          <span className="text-[var(--snoopy-red)] font-bold">gemini</span>한테 다음 라운드를 맡기는 건 어때요?"
        </p>
        <p className="text-xs text-[var(--muted-foreground)] mt-2">— snoopy, 지붕 위에서</p>
      </div>

      <div className="h-4" />

      {cards.map(({ tool, st, worst, suggest }) => (
        <div key={tool.key} className="sticker p-3 flex items-start gap-3">
          <div
            className="w-9 h-9 rounded-full comic-border grid place-items-center shrink-0"
            style={{ background: st.color }}
          >
            <span className="text-lg">{tool.emoji}</span>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-extrabold">{tool.name}</span>
              <span className="text-xs px-1.5 py-0.5 rounded comic-border font-bold" style={{ background: st.color }}>
                {st.label}
              </span>
              <SourceBadge kind={tool.source} />
            </div>
            <p className="text-xs text-[var(--muted-foreground)] mt-1">
              사유: 5h {tool.usage5h}% · 7d {tool.usage7d}% (최대 {worst}%)
            </p>
            <p className="text-xs mt-0.5">
              리셋: <span className="hand">{tool.reset5h}</span> / <span className="hand">{tool.reset7d}</span>
            </p>
            <p className="text-sm font-bold mt-1.5">{suggest}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function QuickStats() {
  const totalCalls = TOOLS.reduce((a, t) => a + t.calls, 0);
  const totalCost = TOOLS.reduce((a, t) => a + t.cost, 0);
  return (
    <div className="sticker p-4 bg-[var(--snoopy-yellow)]">
      <h3 className="hand text-2xl mb-2">오늘의 원장 📒</h3>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-2xl font-extrabold tabular-nums">{totalCalls}</div>
          <div className="text-xs">calls</div>
        </div>
        <div>
          <div className="text-2xl font-extrabold tabular-nums">4.4M</div>
          <div className="text-xs">tokens</div>
        </div>
        <div>
          <div className="text-2xl font-extrabold tabular-nums">${totalCost.toFixed(2)}</div>
          <div className="text-xs">cost</div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Runs ---------------- */

function RunsList({ compact = false }: { compact?: boolean }) {
  return (
    <div className="space-y-2">
      {SAMPLE_RUNS.map((r) => {
        const stateStyle =
          r.state === "running" ? { bg: "var(--snoopy-blue)", label: "running", tc: "text-white" } :
          r.state === "done"    ? { bg: "var(--snoopy-green)", label: "done", tc: "text-white" } :
                                  { bg: "var(--snoopy-red)", label: "stopped_by_guard", tc: "text-white" };
        return (
          <div key={r.id} className="sticker p-3 flex flex-wrap items-center gap-3">
            <span className="font-mono text-sm font-bold">{r.id}</span>
            <span className="text-xs px-2 py-0.5 rounded-full comic-border bg-[var(--paper-dark)] font-bold">
              {r.pattern}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full comic-border font-bold ${stateStyle.tc}`} style={{ background: stateStyle.bg }}>
              {stateStyle.label}
            </span>
            <div className="flex-1 min-w-[160px]">
              <div className="flex justify-between text-xs mb-1">
                <span className="hand">step {r.step}/{r.total}</span>
                <span className="text-[var(--muted-foreground)]">{r.worker}</span>
              </div>
              <div className="h-2 rounded-full comic-border bg-white overflow-hidden">
                <div
                  className="h-full transition-all"
                  style={{
                    width: `${(r.step / r.total) * 100}%`,
                    background: r.state === "stopped_by_guard" ? "var(--snoopy-red)" : "var(--snoopy-green)",
                  }}
                />
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-[var(--muted-foreground)]">SCORE</div>
              <div className="font-extrabold text-lg tabular-nums">{r.score}</div>
            </div>
            {!compact && (
              <button className="sticker px-3 py-1.5 text-xs font-bold bg-[var(--snoopy-yellow)]">
                열기 →
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RunsView() {
  const [selected, setSelected] = useState(SAMPLE_RUNS[0].id);
  const run = SAMPLE_RUNS.find(r => r.id === selected)!;

  return (
    <div className="pt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-1 space-y-3">
        <SectionTitle emoji="▶️" title="런 목록" sub="클릭해서 열어봐" />
        {SAMPLE_RUNS.map((r) => (
          <button
            key={r.id}
            onClick={() => setSelected(r.id)}
            className={`w-full text-left sticker p-3 transition-all ${selected === r.id ? "bg-[var(--snoopy-yellow)]" : "hover:bg-[var(--paper-dark)]"}`}
          >
            <div className="font-mono font-bold text-sm">{r.id}</div>
            <div className="text-xs text-[var(--muted-foreground)] mt-1">{r.pattern} · step {r.step}/{r.total}</div>
          </button>
        ))}
      </div>

      <div className="lg:col-span-2 space-y-6">
        <div className="sticker p-4">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-xl font-extrabold font-mono">{run.id}</h2>
            <span className="text-xs px-2 py-1 rounded-full comic-border bg-[var(--paper-dark)] font-bold">{run.pattern}</span>
            <div className="flex-1" />
            <button className="sticker px-3 py-1.5 text-sm font-bold bg-[var(--snoopy-green)] text-white">▶ Run</button>
            <button className="sticker px-3 py-1.5 text-sm font-bold bg-[var(--snoopy-blue)] text-white">↻ Loop</button>
            <button className="sticker px-3 py-1.5 text-sm font-bold bg-[var(--snoopy-red)] text-white">■ Stop</button>
          </div>
        </div>

        <div className="sticker p-4">
          <h3 className="hand text-xl mb-3">타임라인</h3>
          <ol className="space-y-2">
            {SAMPLE_STEPS.map((s) => {
              const stColor =
                s.status === "done" ? "var(--snoopy-green)" :
                s.status === "running" ? "var(--snoopy-blue)" :
                s.status === "pending" ? "var(--paper-dark)" :
                "var(--snoopy-red)";
              return (
                <li key={s.i} className="flex items-center gap-3 p-2 rounded-lg border-2 border-dashed border-[var(--ink)]/20">
                  <div className="w-9 h-9 rounded-full comic-border grid place-items-center font-extrabold text-sm" style={{ background: stColor }}>
                    #{s.i}
                  </div>
                  <div className="flex-1">
                    <div className="font-bold">{s.worker} <span className="text-xs text-[var(--muted-foreground)] font-normal">· {s.backend}</span></div>
                    <div className="text-xs text-[var(--muted-foreground)]">status: {s.status}{s.issues > 0 && <> · <span className="text-[var(--snoopy-red)] font-bold">⚠ {s.issues} issues</span></>}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[10px] text-[var(--muted-foreground)]">SCORE</div>
                    <div className="font-extrabold text-lg tabular-nums">{s.score ?? "—"}</div>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="sticker p-4">
          <h3 className="hand text-xl mb-2">🪵 run.log</h3>
          <pre className="bg-[var(--ink)] text-[var(--paper)] rounded-lg p-3 text-xs font-mono overflow-auto max-h-64 leading-relaxed">
{`[12:04:11] harness run task-refactor.json (producer-reviewer)
[12:04:12] step #1  claude-main   → drafting patch...
[12:04:33] step #1  done   score=74  issues=2
[12:04:34] step #2  codex-critic  → reviewing...
[12:04:58] step #2  done   score=79  issues=1
[12:04:59] step #3  claude-main   → applying fixes...
[12:05:22] step #3  done   score=82  issues=0
[12:05:23] step #4  codex-critic  → running now...`}
          </pre>
        </div>

        <div className="sticker p-4">
          <h3 className="hand text-xl mb-2">📄 final_output.md</h3>
          <div className="prose prose-sm max-w-none">
            <p className="text-sm text-[var(--muted-foreground)]">아직 진행 중이에요. 완료되면 여기에 최종 산출물이 표시됩니다.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Tasks ---------------- */

function TasksView() {
  const [pattern, setPattern] = useState<"producer-reviewer" | "pipeline" | "fanout-fanin">("producer-reviewer");
  const [task, setTask] = useState("이 리포지토리의 login 함수에서 발견되는 로직 오류를 찾아 수정 패치를 만들어줘.");
  const [producer, setProducer] = useState("claude-main");
  const [reviewer, setReviewer] = useState("codex-critic");
  const [rounds, setRounds] = useState(3);
  const [passScore, setPassScore] = useState(85);

  return (
    <div className="pt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-6">
        <SectionTitle emoji="📝" title="태스크 편집기" sub="패턴을 골라서 실행해" />

        <div className="sticker p-4 space-y-4">
          <div>
            <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">패턴</label>
            <div className="grid grid-cols-3 gap-2 mt-2">
              {(["producer-reviewer","pipeline","fanout-fanin"] as const).map(p => (
                <button
                  key={p}
                  onClick={() => setPattern(p)}
                  className={`sticker p-3 text-sm font-bold text-center transition-all ${pattern === p ? "bg-[var(--snoopy-yellow)]" : "bg-[var(--card)]"}`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">task (목표 프롬프트)</label>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={4}
              className="mt-2 w-full comic-border rounded-lg p-3 bg-white text-sm resize-none focus:outline-none focus:ring-2 focus:ring-[var(--snoopy-red)]"
            />
          </div>

          {pattern === "producer-reviewer" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">producer</label>
                <select value={producer} onChange={(e) => setProducer(e.target.value)} className="mt-2 w-full comic-border rounded-lg p-2 bg-white font-bold">
                  <option>claude-main</option><option>codex-main</option><option>gemini</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">reviewer</label>
                <select value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="mt-2 w-full comic-border rounded-lg p-2 bg-white font-bold">
                  <option>codex-critic</option><option>claude-main</option><option>gemini</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">max_rounds: {rounds}</label>
                <input type="range" min={1} max={10} value={rounds} onChange={(e) => setRounds(+e.target.value)} className="w-full mt-2 accent-[var(--snoopy-red)]" />
              </div>
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">pass_score: {passScore}</label>
                <input type="range" min={50} max={100} value={passScore} onChange={(e) => setPassScore(+e.target.value)} className="w-full mt-2 accent-[var(--snoopy-red)]" />
              </div>
            </div>
          )}

          {pattern === "pipeline" && (
            <div>
              <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">stages (드래그로 정렬)</label>
              <div className="mt-2 space-y-2">
                {["plan","implement","review","polish"].map((s, i) => (
                  <div key={s} className="sticker p-2 flex items-center gap-2 cursor-grab">
                    <span className="hand text-lg">≡</span>
                    <span className="text-xs font-mono bg-[var(--paper-dark)] px-1.5 py-0.5 rounded">#{i+1}</span>
                    <span className="font-bold flex-1">{s}</span>
                    <select className="text-xs comic-border rounded px-2 py-1 bg-white font-bold">
                      <option>claude-main</option><option>codex-main</option><option>gemini</option>
                    </select>
                  </div>
                ))}
                <button className="w-full sticker p-2 text-sm font-bold bg-[var(--snoopy-pink)] hover:bounce-soft">+ 스테이지 추가</button>
              </div>
            </div>
          )}

          {pattern === "fanout-fanin" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">workers[]</label>
                <div className="mt-2 space-y-2">
                  {["claude-main","codex-main","gemini"].map(w => (
                    <label key={w} className="flex items-center gap-2 sticker p-2">
                      <input type="checkbox" defaultChecked className="accent-[var(--snoopy-red)]" />
                      <span className="font-bold text-sm">{w}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">join_worker</label>
                <select className="mt-2 w-full comic-border rounded-lg p-2 bg-white font-bold">
                  <option>gemini</option><option>claude-main</option><option>codex-main</option>
                </select>
              </div>
            </div>
          )}

          <div className="flex gap-2 pt-2 border-t-2 border-dashed border-[var(--ink)]/20">
            <button className="sticker px-4 py-2 font-bold bg-[var(--card)]">💾 저장</button>
            <div className="flex-1" />
            <button className="sticker px-4 py-2 font-bold bg-[var(--snoopy-green)] text-white">▶ Run</button>
            <button className="sticker px-4 py-2 font-bold bg-[var(--snoopy-blue)] text-white">↻ Loop -n 5</button>
          </div>
        </div>
      </div>

      <aside className="space-y-4">
        <SectionTitle emoji="🚦" title="승인 게이트" sub="다음 스텝 실행 전" />
        <div className="sticker p-4 bg-white bubble-tail">
          <p className="hand text-lg">"step #4 실행할까요?<br/>codex-critic이 리뷰합니다."</p>
        </div>
        <div className="h-4" />
        <div className="grid grid-cols-3 gap-2">
          <button className="sticker py-3 font-extrabold bg-[var(--snoopy-green)] text-white">y 승인</button>
          <button className="sticker py-3 font-extrabold bg-[var(--snoopy-yellow)]">n 건너뜀</button>
          <button className="sticker py-3 font-extrabold bg-[var(--snoopy-red)] text-white">q 중단</button>
        </div>

        <SectionTitle emoji="🧑‍🎨" title="워커" sub="backend · role" />
        <div className="space-y-2">
          {[
            { n: "claude-main",   b: "cli",    e: "🦴" },
            { n: "codex-main",    b: "native", e: "✏️" },
            { n: "codex-critic",  b: "native", e: "🕵️" },
            { n: "gemini",        b: "mcp",    e: "🌟" },
          ].map(w => (
            <div key={w.n} className="sticker p-3 flex items-center gap-3">
              <span className="text-2xl">{w.e}</span>
              <div className="flex-1">
                <div className="font-bold">{w.n}</div>
                <div className="text-xs text-[var(--muted-foreground)]">backend: {w.b}</div>
              </div>
              <button className="text-xs font-bold underline">편집</button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

/* ---------------- Settings ---------------- */

function SettingsView({
  soft, setSoft, hard, setHard, guardOn, setGuardOn,
}: {
  soft: number; setSoft: (n: number) => void;
  hard: number; setHard: (n: number) => void;
  guardOn: boolean; setGuardOn: (b: boolean) => void;
}) {
  return (
    <div className="pt-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
      <section className="space-y-4">
        <SectionTitle emoji="🛡️" title="가드" sub="한도 넘기 전에 멈춰" />
        <div className="sticker p-4 space-y-4">
          <label className="flex items-center justify-between">
            <span className="font-bold">enabled</span>
            <button
              onClick={() => setGuardOn(!guardOn)}
              className={`relative w-14 h-7 rounded-full comic-border ${guardOn ? "bg-[var(--snoopy-green)]" : "bg-[var(--paper-dark)]"}`}
            >
              <span className={`absolute top-0.5 w-6 h-6 rounded-full bg-white comic-border transition-all ${guardOn ? "left-7" : "left-0.5"}`} />
            </button>
          </label>
          <div>
            <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">soft_ratio: {soft}%</label>
            <input type="range" min={50} max={100} value={soft} onChange={(e) => setSoft(+e.target.value)} className="w-full mt-2 accent-[var(--snoopy-yellow)]" />
          </div>
          <div>
            <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">hard_ratio: {hard}%</label>
            <input type="range" min={soft} max={120} value={hard} onChange={(e) => setHard(+e.target.value)} className="w-full mt-2 accent-[var(--snoopy-red)]" />
          </div>
          <label className="flex items-center gap-2">
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-[var(--snoopy-red)]" />
            <span className="text-sm font-bold">use_real_limits</span>
          </label>
          <div>
            <label className="text-xs font-bold uppercase text-[var(--muted-foreground)]">on_probe_failure</label>
            <select className="mt-2 w-full comic-border rounded-lg p-2 bg-white font-bold">
              <option>ledger — 원장으로 폴백</option>
              <option>block — 호출 차단</option>
              <option>allow — 그냥 통과</option>
            </select>
          </div>
        </div>

        <SectionTitle emoji="💰" title="예산 (폴백)" sub="daily 한도" />
        <div className="sticker p-4 space-y-3">
          {TOOLS.map(t => (
            <div key={t.key} className="grid grid-cols-4 gap-2 items-center">
              <span className="font-bold">{t.emoji} {t.name}</span>
              <input placeholder="$USD" className="comic-border rounded p-1.5 text-sm bg-white" defaultValue="20" />
              <input placeholder="tokens" className="comic-border rounded p-1.5 text-sm bg-white" defaultValue="3000000" />
              <input placeholder="calls" className="comic-border rounded p-1.5 text-sm bg-white" defaultValue="300" />
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <SectionTitle emoji="🎫" title="요금제" sub="도구별 plan" />
        <div className="sticker p-4 space-y-3">
          {TOOLS.map(t => (
            <div key={t.key} className="flex items-center gap-3">
              <span className="font-bold w-28">{t.emoji} {t.name}</span>
              <select defaultValue={t.plan} className="flex-1 comic-border rounded-lg p-2 bg-white font-bold">
                <option>free</option><option>plus</option><option>pro</option><option>max</option><option>max20</option>
              </select>
            </div>
          ))}
        </div>

        <SectionTitle emoji="🔬" title="한도 probe" sub="도구별 조회 방식" />
        <div className="sticker p-4 space-y-3">
          {[
            { name: "claude", opts: ["claude_transcripts","ledger","command"] },
            { name: "codex",  opts: ["codex_appserver","ledger","command"] },
            { name: "gemini", opts: ["ledger","command"] },
          ].map(t => (
            <div key={t.name} className="flex items-center gap-3">
              <span className="font-bold w-28">{t.name}</span>
              <select className="flex-1 comic-border rounded-lg p-2 bg-white font-bold">
                {t.opts.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>
          ))}
        </div>

        <SectionTitle emoji="👀" title="watch" sub="자동 새로고침" />
        <div className="sticker p-4 flex items-center gap-4">
          <label className="flex items-center gap-2">
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-[var(--snoopy-red)]" />
            <span className="font-bold text-sm">watch 모드</span>
          </label>
          <div className="flex-1">
            <label className="text-xs text-[var(--muted-foreground)]">interval (s)</label>
            <input type="number" defaultValue={5} className="w-24 comic-border rounded p-1.5 ml-2 bg-white" />
          </div>
        </div>
      </section>
    </div>
  );
}

/* ---------------- Knot ---------------- */

const NOTES = [
  { id: "harness-overview", title: "harness 개요", tags: ["core","docs"], links: 4 },
  { id: "guard-policy",     title: "가드 정책 설계",   tags: ["guard"],       links: 3 },
  { id: "codex-appserver",  title: "codex app-server probe 노트", tags: ["probe","codex"], links: 2 },
  { id: "prompt-review",    title: "리뷰어 프롬프트 템플릿", tags: ["prompt"], links: 5 },
];

function KnotView() {
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(NOTES[0].id);
  const filtered = useMemo(() => NOTES.filter(n => n.title.toLowerCase().includes(q.toLowerCase())), [q]);
  const note = NOTES.find(n => n.id === selected)!;

  return (
    <div className="pt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="space-y-3">
        <SectionTitle emoji="🧶" title="knot" sub="지식그물" />
        <div className="sticker p-2 flex items-center gap-2">
          <span>🔎</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="노트 검색..."
            className="flex-1 bg-transparent focus:outline-none text-sm"
          />
        </div>
        <div className="flex gap-2">
          <button className="sticker flex-1 py-2 text-xs font-bold bg-[var(--snoopy-green)] text-white">save</button>
          <button className="sticker flex-1 py-2 text-xs font-bold bg-[var(--snoopy-blue)] text-white">ingest</button>
          <button className="sticker flex-1 py-2 text-xs font-bold bg-[var(--snoopy-yellow)]">lint</button>
        </div>
        <div className="space-y-2">
          {filtered.map(n => (
            <button
              key={n.id}
              onClick={() => setSelected(n.id)}
              className={`w-full text-left sticker p-3 ${selected === n.id ? "bg-[var(--snoopy-yellow)]" : ""}`}
            >
              <div className="font-bold">{n.title}</div>
              <div className="text-xs text-[var(--muted-foreground)] mt-1 flex gap-1 flex-wrap">
                {n.tags.map(t => <span key={t} className="bg-[var(--paper-dark)] px-1.5 py-0.5 rounded">#{t}</span>)}
                <span>· {n.links} links</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="lg:col-span-2 sticker p-6 space-y-4">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-3xl font-extrabold">{note.title}</h2>
          <span className="hand text-[var(--muted-foreground)]">/{note.id}.md</span>
        </div>
        <div className="border-y-2 border-dashed border-[var(--ink)]/20 py-3 text-xs font-mono bg-[var(--paper-dark)] rounded p-3">
{`---
id: ${note.id}
tags: [${note.tags.join(", ")}]
created: 2025-07-04
links: ${note.links}
---`}
        </div>
        <div className="prose max-w-none">
          <p>이 노트는 <b>harness</b>의 핵심 컨셉을 정리합니다. 관련 문서는{" "}
            <a className="text-[var(--snoopy-red)] font-bold underline decoration-wavy" href="#">[[guard-policy]]</a>,{" "}
            <a className="text-[var(--snoopy-red)] font-bold underline decoration-wavy" href="#">[[codex-appserver]]</a>,{" "}
            <a className="text-[var(--snoopy-red)] font-bold underline decoration-wavy" href="#">[[prompt-review]]</a>{" "}
            를 참고하세요.
          </p>
          <p>
            프론트엔드는 별도 API 서버 없이 <b>harness CLI를 subprocess</b>로 호출하고
            <code className="bg-[var(--paper-dark)] px-1 rounded">.harness/</code> 하위 JSON/MD 파일을 읽는 것만으로 완결됩니다.
          </p>
          <ul>
            <li>실측 → <code>codex app-server</code></li>
            <li>추정 → <code>claude transcripts</code> 롤링 합산</li>
            <li>원장 → <code>usage.jsonl</code></li>
          </ul>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Footer ---------------- */

function Footer() {
  return (
    <footer className="border-t-[2.5px] border-[var(--ink)] mt-8 bg-[var(--card)]">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-6 flex flex-wrap items-center gap-4 text-xs">
        <span className="hand text-lg">"happiness is a working guard rail."</span>
        <div className="flex-1" />
        <span className="text-[var(--muted-foreground)]">harness v0.9 · flavor claude · local-only</span>
        <span>🐶</span>
      </div>
    </footer>
  );
}
