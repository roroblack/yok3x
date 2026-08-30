"""V-1 파일럿 — 재사용 가능한 "런북"(StateM 하네스 스케일링, arXiv 2608.15089 이식).

producer가 검증 통과(verify_ok+gate_passed)한 라운드의 접근을 **고정 태그 어휘**로만 요약해
작업 유형(bucket)별로 쌓았다가, 비슷한 신규 작업의 1라운드 프롬프트에 참고 힌트로 주입한다.

설계 제약(계획서 + codex 설계 토론, docs/plans/v4.x-plan-reusable-runbooks-2026-08-24.md 참고):
- 원문 프롬프트·산출물 코드는 절대 저장하지 않는다 — `APPROACH_TAGS`는 사전에 고정된
  유한 집합에서만 골라야 하고, 어휘 밖 문자열은 조용히 버린다(원문 유출 통로 차단).
- 실패·반려된 라운드는 애초에 기록하지 않는다(되돌림 접근을 재사용 후보에서 배제).
- 표본이 min_samples 미만인 bucket은 힌트를 내지 않는다(과적합 방지).
- 재사용 횟수가 많다고 힌트의 신뢰도 문구를 자동으로 올리지 않는다(단조 증가 편향 방지).
- 리뷰어는 producer 프롬프트를 보지 않는 기존 구조 그대로라 힌트 존재는 리뷰어에게 노출되지
  않는다(orchestrator.py의 critic 프롬프트는 산출물+rubric만 받음 — 추가 조치 불필요).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

APPROACH_TAGS = frozenset({
    "tdd-first", "incremental-refactor", "defensive-checks", "edge-case-first",
    "simple-direct", "spec-literal", "performance-first", "readability-first",
})

_RUNBOOKS_FILENAME = "runbooks.jsonl"
_TAG_LINE_RE = re.compile(r"APPROACH_TAGS:\s*(.+)", re.IGNORECASE)


def _runbooks_path(cfg) -> Path:
    return cfg.paths.runs.parent / _RUNBOOKS_FILENAME


def extract_approach_tags(text: str) -> list[str]:
    """producer 응답의 'APPROACH_TAGS: a, b' 줄에서 고정 어휘에 속하는 태그만 뽑는다.

    어휘 밖 문자열(자유 텍스트)은 조용히 버린다 — 저장 전 필터라 원문이 새어나갈 통로가
    없다. 줄이 없거나 형식이 안 맞으면 빈 리스트(정상 — 태그는 선택 사항).
    """
    if not isinstance(text, str):
        return []
    m = _TAG_LINE_RE.search(text)
    if not m:
        return []
    candidates = [t.strip().lower() for t in m.group(1).split(",")]
    return [t for t in candidates if t in APPROACH_TAGS]


def log_runbook_entry(
    cfg, *, run_id: str, bucket: str, pattern: str, approach_tags: list[str],
    verify_ok: bool, gate_passed: bool, score: float | None, rounds: int,
) -> None:
    """검증 통과(verify_ok and gate_passed)한 라운드만, 태그가 하나라도 있을 때만 기록한다."""
    if not (verify_ok and gate_passed):
        return
    tags = sorted({t for t in (approach_tags or []) if t in APPROACH_TAGS})
    if not tags:
        return
    try:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "bucket": str(bucket),
            "pattern": str(pattern),
            "approach_tags": tags,
            "outcome": {"verify_ok": True, "gate_passed": True, "score": score, "rounds": rounds},
        }
        path = _runbooks_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "runbook logging failed: %s: %s", type(exc).__name__, exc)


def query_hint(cfg, *, bucket: str, pattern: str, min_samples: int = 5) -> dict[str, Any]:
    """bucket+pattern별 과거 검증-통과 태그를 집계한다. 표본이 min_samples 미만이면
    사용 불가(insufficient_data)로 명시 반환 — automation.py의 기존 min_samples 패턴과 동일."""
    threshold = min_samples if isinstance(min_samples, int) and not isinstance(min_samples, bool) else 5
    threshold = max(0, threshold)
    matches: list[dict[str, Any]] = []
    try:
        path = _runbooks_path(cfg)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if (isinstance(rec, dict) and rec.get("bucket") == bucket
                        and rec.get("pattern") == pattern):
                    matches.append(rec)
    except (OSError, UnicodeError):
        matches = []

    count = len(matches)
    if count < threshold:
        return {"available": False, "reason": f"samples below min_samples={threshold}",
                "sample_count": count}

    tag_counts: Counter[str] = Counter()
    for rec in matches:
        for t in rec.get("approach_tags") or []:
            if t in APPROACH_TAGS:
                tag_counts[t] += 1
    if not tag_counts:
        return {"available": False, "reason": "no tagged entries", "sample_count": count}

    top_tags = [tag for tag, _ in tag_counts.most_common(2)]
    return {"available": True, "tags": top_tags, "sample_count": count}


def build_hint_text(hint: dict[str, Any]) -> str:
    """참고용 힌트 문구. 지시가 아니라 참고임을 명시하고, 재사용 횟수로 확신을 부풀리지
    않는다(고정 문구 — sample_count가 커져도 어조가 안 바뀜)."""
    if not hint.get("available"):
        return ""
    tags = ", ".join(hint.get("tags") or [])
    if not tags:
        return ""
    return (f"[참고 — 지시 아님, 강제 아님] 비슷한 유형의 과거 작업(표본 {hint.get('sample_count')}건)에서 "
            f"검증 통과로 이어졌던 접근 경향: {tags}. 이 작업에 안 맞으면 무시하라.")
