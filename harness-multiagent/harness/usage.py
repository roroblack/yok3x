"""사용량 원장(usage.jsonl) + 요금 가드 + 사용량 코치.

- 모든 백엔드 호출 결과를 .harness/usage.jsonl에 1행 1이벤트로 기록한다(파일 기반 로그).
- guard: '진짜 한도'(limits.py의 서버 보고 실측)를 최우선으로 보고, 실측이 없으면
  자체 일일 예산(원장)으로 폴백해 soft_ratio 경고 / hard_ratio 루프 자동 정지.
- coach: 세 도구의 5시간/7일 사용률을 비교해 '어느 작업을 · 왜 · 언제' 코칭한다.

'한도는 무조건 지켜야 한다': 실측 probe가 있으면 그 값이 가드의 기준이다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import limits
from .backends import BackendResult
from .config import Config

BACKEND_KEYS = ("claude", "codex", "gemini")


def record(cfg: Config, worker: str, task_kind: str, res: BackendResult) -> None:
    cfg.ensure_dirs()
    row = {
        "ts": time.time(),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "worker": worker,
        "task_kind": task_kind,
        "backend": res.backend,
        "ok": res.ok,
        "cost_usd": res.cost_usd,
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "total_tokens": res.total_tokens,
        "duration_ms": res.duration_ms,
    }
    with cfg.paths.usage_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def today_totals(cfg: Config) -> dict[str, dict[str, float]]:
    """백엔드별 오늘 누적 {usd, tokens, calls}. mock은 claude 예산으로 귀속(usd 기준 시뮬)."""
    totals = {k: {"usd": 0.0, "tokens": 0, "calls": 0} for k in BACKEND_KEYS}
    f = cfg.paths.usage_file
    if not f.exists():
        return totals
    today = datetime.now().strftime("%Y-%m-%d")
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("date") != today:
            continue
        b = row.get("backend", "")
        if b == "mock":
            # mock 호출은 워커의 원 backend 이름 기준으로 귀속하기 위해 worker 매핑 시도
            b = _worker_backend_name(cfg, row.get("worker", "")) or "claude"
        if b not in totals:
            continue
        totals[b]["usd"] += float(row.get("cost_usd", 0) or 0)
        totals[b]["tokens"] += int(row.get("total_tokens", 0) or 0)
        totals[b]["calls"] += 1
    return totals


def _worker_backend_name(cfg: Config, worker: str) -> str | None:
    w = cfg.harness.get("workers", {}).get(worker)
    if not w:
        return None
    name = w.get("backend", "")
    # mock으로 스위치된 경우 워커 이름에서 유추
    if name == "mock":
        for k in BACKEND_KEYS:
            if worker.startswith(k):
                return k
        return "claude"
    return name if name in BACKEND_KEYS else None


# ---------------------------------------------------------------- guard

@dataclass
class GuardVerdict:
    backend: str
    ratio: float          # 가장 높은 사용률(0~). 한도는 '가장 빡빡한 창' 기준
    metric: str           # 그 사용률의 기준 지표
    level: str            # ok | warn | stop
    detail: str
    source: str = "ledger"                    # codex_sessions | claude_transcripts | command | ledger
    real: bool = False                        # 서버 보고 실측이면 True
    reading: limits.LimitReading | None = None  # 실측 원본(coach/mat용)


def _level(g: dict, ratio: float) -> str:
    if not g.get("enabled", True):
        return "ok"
    if ratio >= g.get("hard_ratio", 1.0):
        return "stop"
    if ratio >= g.get("soft_ratio", 0.8):
        return "warn"
    return "ok"


def check_backend(cfg: Config, backend: str) -> GuardVerdict:
    """한도 판정. 실측 probe가 있으면 그것이 기준, 없으면 원장(일일 예산) 폴백."""
    g = cfg.harness["guard"]

    # 1) 진짜 한도 우선 — limits.py 의 서버 보고 실측/롤링 추정
    if g.get("use_real_limits", True):
        reading = limits.probe(cfg, backend)
        if reading.ok and reading.windows:
            ratio = reading.ratio()
            w = reading.worst()
            metric = f"{w.name}·{reading.source}" if w else reading.source
            level = _level(g, ratio)
            tag = "" if reading.real else " (추정)"
            return GuardVerdict(backend, ratio, metric, level,
                                reading.detail + tag, source=reading.source,
                                real=reading.real, reading=reading)
        # 실측 probe가 설정됐으나 실패 → 정책에 따라
        if reading.source not in ("ledger", "disabled", "none") and reading.error:
            policy = g.get("on_probe_failure", "ledger")
            if policy == "block" and g.get("enabled", True):
                return GuardVerdict(backend, 1.0, "probe_fail", "stop",
                                    f"실측 probe 실패 → 차단(fail-closed): {reading.error}",
                                    source=reading.source, real=False, reading=reading)
            # policy == "ledger"(기본) 또는 "allow" → 아래 원장 폴백으로

    # 2) 원장(자체 일일 예산) 폴백
    return _check_backend_ledger(cfg, backend)


def _check_backend_ledger(cfg: Config, backend: str) -> GuardVerdict:
    g = cfg.harness["guard"]
    budgets = cfg.harness["budgets"].get(backend, {})
    totals = today_totals(cfg).get(backend, {"usd": 0, "tokens": 0, "calls": 0})
    ratios: list[tuple[float, str, str]] = []
    if budgets.get("daily_usd"):
        r = totals["usd"] / budgets["daily_usd"]
        ratios.append((r, "daily_usd", f"${totals['usd']:.2f}/${budgets['daily_usd']:.2f}"))
    if budgets.get("daily_tokens"):
        r = totals["tokens"] / budgets["daily_tokens"]
        ratios.append((r, "daily_tokens", f"{totals['tokens']:,}/{budgets['daily_tokens']:,} tok"))
    if budgets.get("daily_calls"):
        r = totals["calls"] / budgets["daily_calls"]
        ratios.append((r, "daily_calls", f"{totals['calls']}/{budgets['daily_calls']} calls"))
    if not ratios:
        return GuardVerdict(backend, 0.0, "-", "ok", "예산 미설정", source="ledger")
    ratio, metric, detail = max(ratios, key=lambda x: x[0])
    level = _level(g, ratio)
    if not g.get("enabled", True):
        detail += " (guard off)"
    return GuardVerdict(backend, ratio, metric, level, detail, source="ledger")


def guard_allows(cfg: Config, worker: str) -> tuple[bool, GuardVerdict]:
    """루프가 이 워커를 지금 호출해도 되는가. stop이면 False → 루프 자동 정지."""
    b = _worker_backend_name(cfg, worker) or "claude"
    v = check_backend(cfg, b)
    return v.level != "stop", v


# ---------------------------------------------------------------- coach

def _time_to_reset() -> str:
    now = datetime.now()
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    dt = reset - now
    h, m = divmod(int(dt.total_seconds()) // 60, 60)
    return f"{h}시간 {m}분 후(자정) 리셋"


def _window(v: GuardVerdict, name: str) -> "limits.Window | None":
    if not v.reading:
        return None
    for w in v.reading.windows:
        if w.name == name:
            return w
    return None


def coach_messages(cfg: Config) -> list[str]:
    """'어느 작업을 · 왜 · 언제' 코칭. 실측 한도가 있으면 5시간/7일 이중 윈도우로 코칭한다."""
    g = cfg.harness["guard"]
    soft = g.get("soft_ratio", 0.8)
    verdicts = {b: check_backend(cfg, b) for b in BACKEND_KEYS}
    routing = cfg.harness.get("routing", {})
    msgs: list[str] = []
    ordered = sorted(verdicts.values(), key=lambda v: v.ratio)
    freest = ordered[0].backend

    for b in BACKEND_KEYS:
        v = verdicts[b]
        tasks_here = "/".join(t for t, bk in routing.items() if bk == b) or "해당"
        alt = freest if freest != b else ordered[1].backend
        w5, w7 = _window(v, "5h"), _window(v, "7d")
        # 어느 작업을 · 왜(사용률) · 언제(리셋) 3요소
        win_str = ""
        if w5 or w7:
            parts = []
            if w5:
                parts.append(f"5시간 {w5.used_percent:.0f}%(리셋 {w5.reset_in()})")
            if w7:
                parts.append(f"7일 {w7.used_percent:.0f}%(리셋 {w7.reset_in()})")
            win_str = " · ".join(parts)
        why = win_str or f"{v.metric} {v.ratio:.0%} ({v.detail})"
        src = "실측" if v.real else ("추정" if v.reading else "원장")

        if v.level == "stop":
            msgs.append(f"[정지·{src}] {b} 한도 초과 — {why}. "
                        f"{tasks_here} 작업을 지금 {alt}로 돌리거나 리셋까지 대기하라.")
        elif v.level == "warn":
            msgs.append(f"[경고·{src}] {b} {why}. "
                        f"비필수 {tasks_here} 작업은 여유 있는 {alt}(사용률 {verdicts[alt].ratio:.0%})로 옮겨라.")
        else:
            # 여유 — 단기 한도가 곧 리셋되면 '지금 큰 작업 밀어붙이기' 코칭
            hint = f"{tasks_here} 등 큰 작업 지금 돌리기 좋다."
            if w5 and w5.resets_at and (w5.reset_in().startswith(("0시간", "1시간"))) and w5.used_percent < soft * 100:
                hint = f"5시간 창이 곧 리셋({w5.reset_in()})되고 사용률도 낮다 — 지금 큰 배치를 밀어붙여라."
            msgs.append(f"[여유·{src}] {b} {why}. {hint}")
    return msgs


def guard_toggle(cfg: Config, on: bool) -> None:
    cfg.harness["guard"]["enabled"] = on
    cfg.save_harness()
