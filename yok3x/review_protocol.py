"""Parsing and normalization for the structured reviewer response protocol."""

from __future__ import annotations

import json
import re
from typing import Any


PROTOCOL_VERSION = "review-v1"
SEVERITIES = frozenset({"critical", "high", "medium", "low"})

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)


def _is_json_object(candidate: str) -> bool:
    try:
        return isinstance(json.loads(candidate), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def _balanced_candidates(text: str):
    """Yield balanced brace substrings, ignoring braces inside JSON strings."""
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
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
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : index + 1]
                    break


def extract_json_candidate(text: str) -> str | None:
    """Find a likely JSON object in reviewer output."""
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


def _normalize_description(description: str) -> str:
    return " ".join(description.split()).casefold()


def canonical_defect_signature(defects: list[dict]) -> tuple[str, ...]:
    """Return an order-independent signature based only on severity and description."""
    return tuple(
        sorted(
            f"{defect['severity']}:{_normalize_description(defect['description'])}"
            for defect in defects
        )
    )
