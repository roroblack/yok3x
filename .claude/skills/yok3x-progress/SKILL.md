---
name: yok3x-progress
description: Show yok3x's remaining/open work as one merged table with two completion percentages (all-time and this-session). Use this whenever the user asks about remaining work, open tasks, "남은 작업", progress, status, or what's left to do in the yok3x repo — and proactively, without being asked, right after finishing a meaningful chunk of work in this repo (a bug fix, a feature, a TeamFlow issue closed), so the user sees the updated picture without having to request it. Don't wait for an explicit "show me a table" — a natural moment to wrap up work is itself a trigger.
---

# yok3x progress table

yok3x tracks its own remaining work in two places that drift out of sync unless merged by hand:
`docs/TODO.md`'s open-items table, and TeamFlow (project key=YOK) as issues — see
`docs/RULE.md` §9 and the `teamflow-issue-tracking` memory for why TeamFlow is used as the real
tracking space. This skill's job is to reconcile both into one table plus two percentages,
every time it's relevant, so nobody has to reconstruct that picture by hand.

## 1. Gather the two sources

**Docs side**: read `docs/TODO.md`, specifically the "지금 열린 작업" table near the top (right
after the title). Each row already carries a TeamFlow key when one exists — use it as the join
key.

**TeamFlow side**: call `mcp__teamflow__list_issues` with `projectId: 89` (project YOK). If that
tool isn't available/connected in this session (it's occasionally not — see the token-scope
incident in `docs/reports/v4.x-assessment-teamflow-token-scope-2026-09-06.md`), fall back to:

```bash
curl -s -H "Authorization: Bearer $(grep TEAMFLOW_API_TOKEN 'C:/Users/playdata2/Documents/test_workspace/jira_for_me/mcp-server/.env' | cut -d= -f2)" \
  "https://jira-for-me.vercel.app/api/issues?projectId=89"
```

Before trusting either path's output, sanity-check identity with `list_projects` (or
`/api/projects`) — it should return project id 89 / key YOK. If it returns something else
(e.g., TFDEV/81), the token in play is wrong for this repo; say so plainly instead of silently
reporting someone else's issue list as yok3x's.

## 2. Merge

Join by TeamFlow key. A TODO.md row with a key that also exists in TeamFlow: use TeamFlow's
`status` as ground truth (it's live; the doc row's prose "상태/막힌 이유" is still useful context,
keep it). A TeamFlow issue with no matching doc row: include it anyway — TODO.md doesn't always
get updated the moment an issue closes. A doc row with no TeamFlow key at all: include it as
doc-only, and flag that it isn't tracked in TeamFlow yet (per RULE §9 it arguably should be —
worth a one-line note, not a lecture).

Only issues/rows with `status != done` are "remaining." Sort the most concretely blocked/
actionable items first (e.g., "waiting on user decision" or "waiting on a resource the user
controls" ahead of "waiting on data to accumulate" — the former is something the user can act
on right now).

## 3. Output table

```markdown
| # | 작업 | TeamFlow | 상태 / 막힌 이유 |
|---|---|---|---|
| 1 | ... | YOK-88 | ... |
```

Keep the "상태/막힌 이유" column short — one line, not a paragraph. If an item is genuinely
just waiting on data/time with nothing for the user to decide, say so plainly so it doesn't read
as more urgent than it is.

## 4. Two percentages

**전체 진행률 (all-time)**: `done` issues ÷ total issues currently in the TeamFlow YOK project.
Get the total from the same `list_issues`/`/api/issues` call (or `list_projects`'s issue count
if exposed) — don't recompute from TODO.md, TeamFlow is the ground truth here per RULE §9.

**이번 세션 진행률**: this one the skill can't see on its own — there's no persisted "session
start" snapshot anywhere, and it shouldn't invent one by writing a file (that would outlive the
conversation and give a wrong answer next time). Instead:

- On the **first** time this skill fires in a conversation, note the current count of open
  (non-done) issues in your own working context (not a file) as the session's starting point,
  and report 세션 진행률 as "0/0 (세션 시작)" or just skip that line with a note that tracking
  starts now.
- On **every subsequent** fire in the same conversation, compare the current open count (and
  which specific keys closed) against that noted starting point: 이번 세션 진행률 = (세션
  시작 시점에 열려 있던 항목 중 지금 done인 것의 수) ÷ (세션 시작 시점에 열려 있던 항목 수).
  Name the specific keys that closed, not just the fraction — "YOK-88, YOK-95 닫음" is more
  useful than "2/9".
- If you (the current instance) don't actually have that starting point in context — e.g. the
  conversation was compacted, or this is a fresh session picking up mid-task — say that plainly
  ("이번 대화에서 세션 시작 스냅샷이 없어 세션 진행률은 생략") rather than guessing or claiming
  0%. A wrong percentage is worse than an honestly missing one.

## Notes

- This is a read-and-report skill — it doesn't close issues or edit TODO.md itself. If closing
  something is obviously warranted (e.g., a TODO.md row's blocker was just resolved earlier in
  this same turn), that's a separate, explicit action — do it via the normal TeamFlow tools and
  say so, don't fold it silently into "just showing a table."
- Don't re-derive the whole `docs/TODO.md` history section (T-1, T-2, etc.) — only the "지금
  열린 작업" table at the top is in scope; the rest is background/rationale, not a to-do list.
