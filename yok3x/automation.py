"""Automation-mode contracts and deterministic S2 task recommendations."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

AUTOMATION_MODES = frozenset(("off", "assist", "full"))
AUTOMATION_OPTION_DEFAULTS = {
    "max_rounds_cap": 4, "min_rounds": 1,
    "allow_backend_reallocation": False,
    "allow_effort_adjustment": False, "calibration_window": 20,
    "low_confidence_action": "assist",
}
EXPLICIT_TASK_FIELDS = frozenset(
    ("producer", "reviewer", "backend", "model", "effort", "max_rounds", "pass_score")
)


def validate_automation_mode(value: Any, *, source: str = "automation_mode") -> str:
    if not isinstance(value, str) or value not in AUTOMATION_MODES:
        raise ValueError(f"{source} must be one of off|assist|full; got {value!r}")
    return value


def validate_automation_config(config: Mapping[str, Any]) -> None:
    validate_automation_mode(config.get("automation_mode", "off"))
    options = config.get("automation", {})
    if not isinstance(options, Mapping):
        raise ValueError("automation must be an object")
    for key, default in AUTOMATION_OPTION_DEFAULTS.items():
        value = options.get(key, default)
        if key in ("allow_backend_reallocation", "allow_effort_adjustment"):
            if not isinstance(value, bool):
                raise ValueError(f"automation.{key} must be boolean; got {value!r}")
        elif key in ("max_rounds_cap", "min_rounds", "calibration_window"):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"automation.{key} must be a non-negative integer; got {value!r}")
        elif key == "low_confidence_action" and value != "assist":
            raise ValueError("automation.low_confidence_action must be assist")


def _config_mapping(config: Any) -> Mapping[str, Any]:
    if config is None:
        return {}
    return config.yok3x if hasattr(config, "yok3x") else config


def effective_automation_decision(task_spec: Mapping[str, Any] | None = None,
                                  config: Any = None) -> dict[str, Any]:
    """Return S1's effective mode and the explicit-field protection map."""
    task = task_spec or {}
    global_config = _config_mapping(config)
    global_mode = validate_automation_mode(global_config.get("automation_mode", "off"))
    if "automation_mode" in task:
        mode = validate_automation_mode(task["automation_mode"], source="task automation_mode")
        mode_source = "task"
    else:
        mode = global_mode
        mode_source = "global" if "automation_mode" in global_config else "default"
    explicit = {field: field in task for field in EXPLICIT_TASK_FIELDS}
    return {
        "mode": mode, "effective_mode": mode, "mode_source": mode_source,
        "explicit_fields": tuple(sorted(f for f, present in explicit.items() if present)),
        "fields": {f: {"protected": present,
                        "source": "explicit" if present else "profile/routing/default"}
                    for f, present in sorted(explicit.items())},
    }


def resolve_effective_mode(task_spec: Mapping[str, Any] | None = None,
                           config: Any = None) -> str:
    return effective_automation_decision(task_spec, config)["effective_mode"]


def validate_task_automation_mode(task_spec: Mapping[str, Any]) -> None:
    if "automation_mode" in task_spec:
        validate_automation_mode(task_spec["automation_mode"], source="task automation_mode")


# S2 deliberately has no calibration-file dependency.  Keep these constants
# small and explicit so the recommendation remains auditable and reproducible.
_TEXT_FIELDS = ("task", "brief", "rubric", "examples")
_RISK_TERMS = (
    "refactor", "review", "security", "역직렬화", "동시성", "마이그레이션",
    "migration", "concurrency", "deserializ",
)
_PATTERN_COMPLEXITY = {
    "producer-reviewer": 1,
    "pipeline": 3,
    "fanout": 3,
    "fanout-fanin": 4,
}


def _bounded_text(value: Any, limit: int = 100_000) -> str:
    """Convert arbitrary task fields to bounded, deterministic text."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)[:limit]
    if isinstance(value, Mapping):
        # Dict insertion order is user-controlled; sorted keys make the signal
        # stable even when a spec was assembled in a different order.
        return " ".join(f"{k}:{value[k]}" for k in sorted(value, key=str))[:limit]
    if isinstance(value, (set, frozenset)):
        return " ".join(_bounded_text(item, limit) for item in sorted(value, key=str))[:limit]
    if isinstance(value, (list, tuple)):
        return " ".join(_bounded_text(item, limit) for item in value)[:limit]
    return str(value)[:limit]


def _sequence_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (str, bytes, Mapping)):
        return 1 if value else 0
    try:
        return min(len(value), 1000)
    except TypeError:
        return 1


def calculate_task_features(
    task_spec: Mapping[str, Any] | None = None,
    *,
    base_dir: str | Path | None = None,
    calibration: Any = None,
) -> dict[str, Any]:
    """Return bounded structural S2 features for a task spec.

    ``calibration`` is accepted as a forward-compatible S3 seam and is
    intentionally ignored in S2.  No task content is persisted by this
    function.
    """
    del calibration
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    text_lengths = {field: len(_bounded_text(spec.get(field))) for field in _TEXT_FIELDS}
    input_chars = min(sum(text_lengths.values()), 400_000)

    globs = spec.get("context_globs")
    glob_patterns = _sequence_count(globs)
    context_files = glob_patterns
    if base_dir is not None and globs:
        root = Path(base_dir)
        values = [globs] if isinstance(globs, str) else list(globs) if not isinstance(globs, Mapping) else [globs]
        context_files = 0
        for pattern in values[:1000]:
            try:
                context_files += sum(1 for path in root.glob(str(pattern)) if path.is_file())
            except (OSError, RuntimeError, ValueError):
                context_files += 1
        context_files = min(context_files, 1000)

    pattern = str(spec.get("pattern", "")).casefold()
    pattern_score = _PATTERN_COMPLEXITY.get(pattern, 0)
    stages = _sequence_count(spec.get("stages"))
    workers = _sequence_count(spec.get("workers"))
    join_worker = 1 if spec.get("join_worker") else 0
    acquire = spec.get("acquire")
    qa_count = 0
    if isinstance(acquire, Mapping):
        for key in ("qa", "questions", "checks"):
            if key in acquire:
                qa_count += _sequence_count(acquire.get(key))
        if not qa_count and acquire:
            qa_count = 1
    verify = bool(spec.get("verify_cmd"))
    materialize = bool(spec.get("materialize"))
    structure_score = min(
        pattern_score + min(stages, 10) + min(workers, 10) + join_worker
        + min(qa_count, 10) + (2 if verify else 0) + (2 if materialize else 0),
        40,
    )
    searchable = " ".join(_bounded_text(spec.get(field)) for field in _TEXT_FIELDS).casefold()
    risk_terms = tuple(term for term in _RISK_TERMS if term.casefold() in searchable)
    risk = bool(risk_terms)
    return {
        "input_chars": input_chars,
        "text_lengths": text_lengths,
        "context_glob_count": glob_patterns,
        "context_file_count": context_files,
        "pattern": pattern,
        "pattern_score": pattern_score,
        "stages": stages,
        "workers": workers,
        "join_worker": join_worker,
        "acquire_qa": qa_count,
        "has_verify_cmd": verify,
        "has_materialize": materialize,
        "structure_score": structure_score,
        "risk_terms": risk_terms,
        "risk": risk,
    }


def _automation_options(config: Any) -> Mapping[str, Any]:
    root = _config_mapping(config)
    options = root.get("automation", {}) if isinstance(root, Mapping) else {}
    return options if isinstance(options, Mapping) else {}


def recommend_effort_rounds(
    task_spec: Mapping[str, Any] | None = None,
    config: Any = None,
    *,
    calibration: Any = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Recommend deterministic effort and rounds from S2 structural features."""
    del calibration
    features = calculate_task_features(task_spec, base_dir=base_dir)
    chars = features["input_chars"]
    complexity = features["structure_score"]
    if features["risk"]:
        bucket = "risk"
        effort, round_candidates, selected_round = "high", (3, 4), 4
    elif chars <= 200 and complexity <= 1:
        bucket = "tiny"
        effort, round_candidates, selected_round = "low", (1, 2), 1
    elif chars <= 1_000 and complexity <= 4:
        bucket = "small"
        effort, round_candidates, selected_round = "medium", (1, 2), 2
    elif chars <= 4_000 and complexity <= 8:
        bucket = "medium"
        effort, round_candidates, selected_round = "medium", (2, 3), 2
    else:
        bucket = "large"
        effort, round_candidates, selected_round = "high", (3, 4), 3

    options = _automation_options(config)
    cap = options.get("max_rounds_cap", AUTOMATION_OPTION_DEFAULTS["max_rounds_cap"])
    minimum = options.get("min_rounds", AUTOMATION_OPTION_DEFAULTS["min_rounds"])
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        cap = AUTOMATION_OPTION_DEFAULTS["max_rounds_cap"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        minimum = AUTOMATION_OPTION_DEFAULTS["min_rounds"]
    rounds = min(max(selected_round, minimum), cap)
    reason = (
        f"bucket={bucket}; input_chars={chars}; structure_score={complexity}; "
        f"pattern={features['pattern'] or 'unknown'}; risk_terms={','.join(features['risk_terms']) or 'none'}; "
        f"candidates=effort:{effort},rounds:{round_candidates[0]}-{round_candidates[1]}; "
        f"rounds_clamp={cap}"
    )
    return {
        "bucket": bucket,
        "effort": effort,
        "effort_candidates": ("low", "medium") if bucket in ("tiny", "small") else
        (("medium",) if bucket == "medium" else ("medium", "high")),
        "rounds": rounds,
        "round_candidates": round_candidates,
        "confidence": 0.9 if features["risk"] else (0.8 if complexity or chars else 0.7),
        "reason": reason,
        "features": features,
    }


# Descriptive aliases keep the public API discoverable without duplicating logic.
compute_task_features = calculate_task_features
recommend_automation = recommend_effort_rounds
