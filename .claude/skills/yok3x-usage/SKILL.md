---
name: yok3x-usage
description: How to invoke yok3x (a producer-reviewer multi-agent coding orchestrator with objective test gating and quota-aware pacing) safely from another agent or automated caller — including OpenClaw, Hermes, or any always-on personal-agent runtime that might trigger a run from a message, cron job, or webhook without a human watching at that moment. Use this whenever you need to delegate a repeatable coding task to yok3x, or when deciding whether an unattended/automated invocation is safe to make.
---

# Using yok3x from another agent

yok3x is a CLI tool (`python yok3x.py ...`, repo at the path this file lives under). It runs a
producer LLM (writes code) and a reviewer LLM (scores it), optionally gated by an objective
`verify_cmd` (e.g. `pytest -q`) that actually runs the candidate's tests — it is not just "ask a
second model for its opinion." It also tracks per-backend quota/rate limits and can pace or
degrade itself near a limit.

**Because it is just a CLI, any agent with shell access — OpenClaw, Hermes, or your own script —
can already invoke it.** No special integration layer is required for basic use. What matters is
invoking it *safely* when the caller is unattended (nobody is watching this specific invocation in
real time), which is the normal operating mode for OpenClaw/Hermes-style always-on agents
triggered by a chat message, cron job, or webhook.

## The one rule that matters: never combine unattended calls with unlimited spend

yok3x's approval gate (`--auto`) and its per-run cost cap
(`guard.reservation.max_usd_per_run`, default `0` = unlimited) are independent settings. Nothing
stops you from running `--auto` with no cap set — that combination means real money can be spent
with no human in the loop and no ceiling. **Do not do this.** Use `--unattended` instead of
plain `--auto` for any call an external agent triggers on its own:

```bash
python yok3x.py run task.json --unattended
```

`--unattended` implies `--auto` (an unattended caller can't answer an interactive y/n prompt
anyway) and **fails closed before calling any worker** if `guard.reservation.max_usd_per_run` is
not set to a positive number in the resolved config — you'll get
`aborted: ... (cause=unattended_requires_cost_cap)` instead of a run. Set a real cap first:

```json
{"guard": {"reservation": {"max_usd_per_run": 1.50}}}
```

(in `yok3x.json`, or wherever your task's config comes from). Pick a number you're actually
willing to lose if something goes wrong — this is a hard ceiling on that one run, not an
estimate.

The run's `status.json` will carry `"unattended": true` when this path was used, so you (or a
human later) can always tell which runs were triggered by an unattended caller versus a person
typing at a terminal.

## Minimal task.json

```json
{
  "pattern": "producer-reviewer",
  "task": "<natural-language description of what to build/fix>",
  "producer": "claude-main",
  "reviewer": "codex-critic",
  "max_rounds": 2,
  "pass_score": 8.0,
  "score_gate_mode": "strict",
  "verify_cmd": "python -m pytest -q",
  "changes": {"mode": "review"},
  "workdir": "<absolute path to an isolated directory — never the yok3x repo itself>"
}
```

Strongly recommended for automated callers, even though not hard-enforced by `--unattended`:
- **Always set `workdir`** to a directory that isn't the yok3x repo and isn't shared with other
  concurrent work — running from the wrong directory silently loads the wrong config (a real
  incident, see `docs/reports/v4.x-result-t2-round2-verify-ok-false-diagnosis-2026-08-30.md`
  "관찰 3").
- **Always set `verify_cmd`** with `score_gate_mode: "strict"` when there's any way to write an
  objective test for the task — this is yok3x's actual differentiator over "just ask another
  model." A run with no `verify_cmd` and `score_gate_mode: "advisory"` is trusting the reviewer's
  opinion alone, which is a weaker guarantee.

## Checking quota before you call

If you're an always-on agent that might invoke yok3x frequently, check remaining quota first so
you don't kick off a run into a backend that's already near its limit:

```bash
python yok3x.py limits --json
```

Look at `backends.<name>.level` (`ok`/`warn`/`stop`) and `.provenance` (`measured` is trustworthy;
`estimated` and `unavailable` less so — see
`docs/reports/v4.x-result-t2-round2-verify-ok-false-diagnosis-2026-08-30.md`-adjacent
`limits.py` provenance model). Don't trigger a new run if the relevant backend is at `stop`.

## What NOT to do

- Don't set `auto_approve: true` globally in `yok3x.json` just to make an external agent's calls
  work — that removes the approval gate for every future interactive use too, not just the
  automated path. Use `--unattended` per-call instead.
- Don't invoke yok3x with `--auto` (not `--unattended`) from an unattended trigger just because
  it's shorter to type — that's exactly the unlimited-spend combination this skill exists to
  prevent.
- Don't run yok3x from inside its own repo directory as the task `workdir` — see the `workdir`
  note above.
- Don't assume a `codex`/`claude` backend reachable from this shell is the same account as any
  other integration's credentials (e.g. a shared TeamFlow token) — cross-tool credential sharing
  caused a real incident here; see `docs/reports/v4.x-assessment-teamflow-token-scope-2026-09-06.md`.
  Give yok3x's own invocations their own credentials/env, don't borrow another tool's.
