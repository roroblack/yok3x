"""mat — 사용량·코칭·워커 진행 상태를 한 화면에서 확인하는 모니터링 뷰.

환경변수 없이 harness.json 한 파일로 동작한다. --watch로 주기 갱신.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from . import usage
from .config import Config

BAR_W = 18


def _bar(ratio: float) -> str:
    r = max(0.0, min(ratio, 1.0))
    filled = int(round(r * BAR_W))
    return "█" * filled + "░" * (BAR_W - filled)


def _fmt_budget_line(cfg: Config, backend: str) -> str:
    v = usage.check_backend(cfg, backend)
    mark = {"ok": " ", "warn": "!", "stop": "X"}[v.level]
    src = {"codex_appserver": "실측", "codex_sessions": "실측", "claude_transcripts": "추정",
           "command": "실측", "ledger": "원장", "disabled": "원장"}.get(v.source, v.source)
    head = f" {mark} {backend:<7} {_bar(v.ratio)} {v.ratio:>4.0%}  [{src}]"
    # 실측 윈도우가 있으면 5h/7d 를 함께 표시
    if v.reading and v.reading.windows:
        wins = "  ".join(f"{w.name} {w.used_percent:.0f}%(리셋 {w.reset_in()})"
                         for w in v.reading.windows)
        return head + "  " + wins
    totals = usage.today_totals(cfg)[backend]
    return head + f"  {v.detail}   (calls {totals['calls']})"


def _recent_runs(cfg: Config, n: int = 5) -> list[dict[str, Any]]:
    runs = []
    if not cfg.paths.runs.exists():
        return runs
    for d in sorted(cfg.paths.runs.iterdir(), reverse=True)[:n]:
        st = d / "status.json"
        if st.exists():
            try:
                runs.append(json.loads(st.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
    return runs


def render(cfg: Config) -> str:
    g = cfg.harness["guard"]
    lines = []
    lines.append("╭─ mat · 하네스 멀티 에이전트 v2.2 ─ "
                 + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ─╮")
    lines.append(f"  flavor: {cfg.harness['flavor']}"
                 f"   guard: {'ON' if g.get('enabled', True) else 'OFF'}"
                 f" (warn {g['soft_ratio']:.0%} / stop {g['hard_ratio']:.0%})")
    lines.append("")
    lines.append("  [오늘 사용량 — 일일 예산 대비]")
    for b in usage.BACKEND_KEYS:
        lines.append(_fmt_budget_line(cfg, b))
    lines.append("")
    lines.append("  [coach]")
    for m in usage.coach_messages(cfg):
        lines.append(f"   · {m}")
    lines.append("")
    lines.append("  [최근 런 / 워커 진행 상태]")
    runs = _recent_runs(cfg)
    if not runs:
        lines.append("   (기록 없음 — `harness run <task.json>` 실행)")
    for r in runs:
        steps = r.get("steps", [])
        done = sum(1 for s in steps if s["status"] == "done")
        cur = steps[-1]["worker"] if steps else "-"
        lines.append(f"   {r['run_id']}  [{r.get('pattern', r.get('state'))}]"
                     f"  state={r['state']}  step {done}/{len(steps)}  last={cur}")
        for s in steps[-3:]:
            score = f" score={s['score']}" if s.get("score") is not None else ""
            issues = f"  ⚠ {'; '.join(s['checklist'])}" if s.get("checklist") else ""
            lines.append(f"     └ #{s['index']:02d} {s['worker']:<13} {s['status']}{score}{issues}")
    lines.append("╰" + "─" * 58 + "╯")
    return "\n".join(lines)


def show(cfg: Config, watch: bool = False, interval: float = 2.0) -> None:
    if not watch:
        print(render(cfg))
        return
    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            print(render(cfg))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
