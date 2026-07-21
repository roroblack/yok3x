#!/usr/bin/env python3
"""docs/TODO.md 자동 점검 — 하루 1회 실행 상정. 의존성 0(stdlib만).

지금 유일한 대기 항목: '심판 캘리브레이션 실데이터가 충분히 쌓였나'.
쌓이면 (1) yok3x calib로 SCORE↔verify 상관 확인 (2) 작업E(blind 리뷰어)를 A/B 검증
(3) 게이트 임계 8.0 재설정 판단. 그 전엔 전부 근거 없는 추측이므로 대기한다.

이 스크립트는 판단하지 않는다 — 데이터를 세고, 기준 충족 여부만 보고하고,
docs/TODO.md의 '자동 점검 상태' 블록을 갱신한다. (읽기 전용 계측 + 상태표시만.)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALIB = ROOT / ".yok3x" / "calibration.jsonl"
TODO = ROOT / "docs" / "TODO.md"

# ---- '충분한 데이터' 기준 (사전 고정) --------------------------------------
# 왜 이 값인가:
# - MIN_RUNS=10: calibration.summarize가 n<10을 '표본 부족'으로 판정하는 우리 자체 기준.
#   단 '행'이 아니라 '독립 런(run_id)'으로 센다 — 한 런의 여러 라운드는 상관되므로(codex).
# - MIN_PER_CLASS=3: verify 통과/실패가 한쪽뿐이면 상관·혼동행렬이 정의되지 않는다.
#   양쪽 최소 3개씩은 있어야 퇴화하지 않는다.
MIN_RUNS = 10
MIN_PER_CLASS = 3


def load_records() -> list[dict]:
    if not CALIB.exists():
        return []
    out = []
    for line in CALIB.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # 손상된 행은 건너뛴다(계측이 판단을 오염시키지 않게)
    return out


def independent_labeled(records: list[dict]) -> list[dict]:
    """런당 1관측 = 그 런의 라벨된(verify_ok가 bool, score가 수) 최종 라운드 레코드.
    독립성 확보를 위해 run_id로 묶고 최대 round를 대표로 쓴다.
    F1-b: verify가 후보를 검증한(verify_scope=='candidate') 레코드만 유효 라벨.
    original_tree/None(과거·F1-d 이전)은 준비도 과대계상을 막기 위해 제외한다."""
    by_run: dict[str, dict] = {}
    for r in records:
        rid = r.get("run_id")
        s, v = r.get("score"), r.get("verify_ok")
        if (r.get("verify_scope") != "candidate"
                or rid is None or not isinstance(v, bool)
                or not isinstance(s, (int, float))):
            continue
        cur = by_run.get(rid)
        if cur is None or (r.get("round") or 0) >= (cur.get("round") or 0):
            by_run[rid] = r
    return list(by_run.values())


def evaluate() -> dict:
    records = load_records()
    labeled = independent_labeled(records)
    n_pass = sum(1 for r in labeled if r["verify_ok"] is True)
    n_fail = sum(1 for r in labeled if r["verify_ok"] is False)
    ready = (len(labeled) >= MIN_RUNS
             and n_pass >= MIN_PER_CLASS and n_fail >= MIN_PER_CLASS)
    return {
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "calib_exists": CALIB.exists(),
        "total_rows": len(records),
        "independent_runs": len(labeled),
        "verify_pass": n_pass,
        "verify_fail": n_fail,
        "ready": ready,
        "need_runs": max(0, MIN_RUNS - len(labeled)),
        "need_pass": max(0, MIN_PER_CLASS - n_pass),
        "need_fail": max(0, MIN_PER_CLASS - n_fail),
    }


def render_status(s: dict) -> str:
    verdict = ("✅ **점검 가능 — 데이터 충족.** `yok3x calib` 실행 + 작업E A/B 검증 착수"
               if s["ready"] else
               f"⏳ **대기 — 데이터 부족.** 독립런 {s['independent_runs']}/{MIN_RUNS} · "
               f"통과 {s['verify_pass']}/{MIN_PER_CLASS} · 실패 {s['verify_fail']}/{MIN_PER_CLASS}")
    lines = [
        "<!-- AUTO:todo_check START (scripts/todo_check.py가 자동 갱신) -->",
        f"### 자동 점검 상태 · {s['checked_at']}",
        "",
        verdict,
        "",
        f"- calibration.jsonl: {'있음' if s['calib_exists'] else '없음(실런 0)'} · 전체 {s['total_rows']}행",
        f"- 독립 런(run_id): **{s['independent_runs']}** (기준 ≥{MIN_RUNS})",
        f"- verify 통과 {s['verify_pass']} · 실패 {s['verify_fail']} (각 기준 ≥{MIN_PER_CLASS})",
        "<!-- AUTO:todo_check END -->",
    ]
    return "\n".join(lines)


def update_todo(status_block: str) -> bool:
    if not TODO.exists():
        return False
    text = TODO.read_text(encoding="utf-8")
    pat = re.compile(r"<!-- AUTO:todo_check START.*?<!-- AUTO:todo_check END -->",
                     re.DOTALL)
    new = pat.sub(status_block, text) if pat.search(text) else text.rstrip() + "\n\n" + status_block + "\n"
    if new != text:
        TODO.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> int:
    s = evaluate()
    block = render_status(s)
    changed = update_todo(block)
    print(block.replace("<!-- AUTO:todo_check START (scripts/todo_check.py가 자동 갱신) -->\n", "")
              .replace("\n<!-- AUTO:todo_check END -->", ""))
    print(f"\n(docs/TODO.md {'갱신됨' if changed else '변화 없음'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
