"""Parsing and normalization for the structured reviewer response protocol."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "review-v1"
SEVERITIES = frozenset({"critical", "high", "medium", "low"})

# T-1 실측(v4.x-result-t1-pilot-spec-test-separation-2026-08-30): 리뷰어가 직접 내는 SCORE는
# 완전히 동일한 결함 목록에도 큰 분산을 보일 수 있다. 이 기본값은 그 실측에서 쓴 값 그대로다 —
# 임의의 숫자이므로 cfg.yok3x["review_protocol"]에서 재정의 가능해야 한다(orchestrator가 전달).
DEFAULT_SEVERITY_WEIGHTS = {"critical": 5.0, "high": 2.0, "medium": 0.5, "low": 0.1}
DEFAULT_SEVERITY_CAPS = {"critical": 4.0, "high": 7.0, "medium": 9.0}

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)
_OBSERVATION_FILENAME = "review_protocol_observations.jsonl"
_OBSERVATION_SOURCES = frozenset({"structured", "legacy_text"})


def _observation_path(cfg) -> Path:
    return cfg.paths.runs.parent / _OBSERVATION_FILENAME


def log_observation(cfg, *, run_id, reviewer, source, parse_error=None) -> None:
    """Append minimal structured-review telemetry without retaining review text."""
    try:
        if source not in _OBSERVATION_SOURCES:
            raise ValueError(f"unknown review protocol source: {source!r}")
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "reviewer": reviewer,
            "source": source,
            "parse_error": (str(parse_error) if parse_error is not None else None),
        }
        path = _observation_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "review protocol observation logging failed: %s: %s", type(exc).__name__, exc
        )


def summarize_review_protocol_observations(cfg, *, window: int = 50) -> dict:
    """Summarize the most recent valid review-protocol observations."""
    records: list[dict[str, Any]] = []
    try:
        path = _observation_path(cfg)
        if path.is_file():
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                        if (
                            isinstance(record, dict)
                            and record.get("source") in _OBSERVATION_SOURCES
                            and "reviewer" in record
                        ):
                            records.append(record)
                    except (json.JSONDecodeError, TypeError):
                        continue
    except (OSError, UnicodeError):
        records = []

    try:
        limit = max(0, int(window))
    except (TypeError, ValueError):
        limit = 50
    records = records[-limit:] if limit else []
    total = len(records)
    source_counts = Counter(record["source"] for record in records)
    parse_errors = Counter(
        str(record["parse_error"])
        for record in records
        if record.get("parse_error") is not None
    )

    def ratios(counts: Counter, count: int) -> dict[str, Any]:
        return {
            "total": count,
            "structured": counts.get("structured", 0),
            "legacy_text": counts.get("legacy_text", 0),
            "structured_ratio": counts.get("structured", 0) / count if count else 0.0,
            "legacy_text_ratio": counts.get("legacy_text", 0) / count if count else 0.0,
        }

    reviewer_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        reviewer_records[str(record.get("reviewer"))].append(record)
    reviewers = {}
    for reviewer, items in reviewer_records.items():
        counts = Counter(item["source"] for item in items)
        reviewers[reviewer] = ratios(counts, len(items))
        reviewers[reviewer]["parse_errors"] = dict(Counter(
            str(item["parse_error"])
            for item in items
            if item.get("parse_error") is not None
        ))

    return {
        "status": "ok" if total >= 5 else "insufficient_data",
        "total": total,
        "sample_count": total,
        "structured": source_counts.get("structured", 0),
        "legacy_text": source_counts.get("legacy_text", 0),
        "structured_ratio": source_counts.get("structured", 0) / total if total else 0.0,
        "legacy_text_ratio": source_counts.get("legacy_text", 0) / total if total else 0.0,
        "source_counts": dict(source_counts),
        "parse_errors": dict(parse_errors),
        "reviewers": reviewers,
    }


def _is_json_object(candidate: str) -> bool:
    try:
        return isinstance(json.loads(candidate), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def _balanced_candidates(text: str):
    """Yield balanced brace substrings, ignoring braces inside JSON strings."""
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, current in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
            continue
        if current == '"':
            in_string = True
        elif current == "{":
            if depth == 0:
                start = index
            depth += 1
        elif current == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]
                start = None


def extract_json_candidate(text: str) -> str | None:
    """Find a likely JSON object in reviewer output."""
    if not isinstance(text, str):
        return None
    if _is_json_object(text):
        return text

    fenced = list(_FENCE_RE.finditer(text))
    for match in fenced:
        content = match.group(1).strip()
        if _is_json_object(content):
            return content

    balanced = list(_balanced_candidates(text))
    for candidate in balanced:
        if _is_json_object(candidate):
            return candidate
    return balanced[0] if balanced else None


def _failure(raw_text: str, reason: str) -> dict[str, Any]:
    return {
        "source": "legacy_text",
        "protocol_version": None,
        "score": None,
        "defects": None,
        "summary": None,
        "raw_text": raw_text,
        "parse_error": reason,
    }


def parse_review_response(text: str) -> dict[str, Any]:
    """Parse one response, falling back atomically to the legacy text format."""
    candidate = extract_json_candidate(text)
    if candidate is None:
        return _failure(text, "no JSON object candidate found")
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError) as exc:
        return _failure(text, f"invalid JSON: {exc.msg if isinstance(exc, json.JSONDecodeError) else exc}")

    if not isinstance(payload, dict):
        return _failure(text, "top-level JSON value must be an object")
    if not isinstance(payload.get("protocol_version"), str):
        return _failure(text, "protocol_version must be a string")
    defects = payload.get("defects")
    if not isinstance(defects, list):
        return _failure(text, "defects must be a list")

    canonical: list[dict[str, str]] = []
    for position, defect in enumerate(defects):
        if not isinstance(defect, dict):
            return _failure(text, f"defect {position} must be an object")
        severity = defect.get("severity")
        if severity not in SEVERITIES:
            return _failure(text, f"defect {position} has invalid severity")
        description = defect.get("description")
        if not isinstance(description, str) or not description.strip():
            return _failure(text, f"defect {position} description must be a non-empty string")
        for field in ("evidence", "fix"):
            if field in defect and not isinstance(defect[field], str):
                return _failure(text, f"defect {position} {field} must be a string")
        canonical.append(
            {
                "severity": severity,
                "description": description,
                "evidence": defect.get("evidence", ""),
                "fix": defect.get("fix", ""),
            }
        )

    score = payload.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
            return _failure(text, "score must be a number between 0 and 10")
        score = float(score)

    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        return _failure(text, "summary must be a string")
    return {
        "source": "structured",
        "protocol_version": payload["protocol_version"],
        "score": score,
        "defects": canonical,
        "summary": summary,
        "raw_text": text,
        "parse_error": None,
    }


def compute_deterministic_score(
    defects: list[dict], *, weights: dict[str, float] | None = None,
    caps: dict[str, float] | None = None,
) -> float:
    """결함 목록만으로 SCORE를 결정론적으로 계산한다(리뷰어의 자유형 SCORE 대체용).

    같은 결함 목록이면 항상 같은 점수를 낸다(순수 함수) — "결함 탐지" 자체의 분산은
    줄이지 못하지만(입력이 다르면 결과도 다름), "같은 결함을 보고도 점수 환산이 오락가락"하는
    분산은 없앤다(T-1 실측으로 확인 — v4.x-result-t1-pilot-spec-test-separation-2026-08-30).
    """
    weights = weights if weights is not None else DEFAULT_SEVERITY_WEIGHTS
    caps = caps if caps is not None else DEFAULT_SEVERITY_CAPS
    penalty = sum(float(weights.get(d.get("severity"), 0.0)) for d in defects if isinstance(d, dict))
    score = max(0.0, 10.0 - penalty)
    for severity in ("critical", "high", "medium"):
        if any(isinstance(d, dict) and d.get("severity") == severity for d in defects):
            score = min(score, float(caps.get(severity, 10.0)))
            break
    return round(score * 2) / 2.0


def _normalize_description(description: Any) -> str:
    return " ".join(str(description if description is not None else "").split()).casefold()


def canonical_defect_signature(defects: list[dict]) -> tuple[str, ...]:
    """Return an order-independent signature based only on severity and description."""
    return tuple(
        sorted(
            f"{defect.get('severity', '')}:{_normalize_description(defect.get('description'))}"
            for defect in defects
            if isinstance(defect, dict)
        )
    )
