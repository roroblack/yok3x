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


def _json_safe(value: Any) -> Any:
    """Return a stable JSON-compatible copy of an automation result."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_automation_decision_snapshot(
    spec: Mapping[str, Any] | None = None,
    config: Any = None,
    *,
    pace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the explainable, display-only automation decision snapshot."""
    decision = effective_automation_decision(spec, config)
    mode = decision["effective_mode"]
    if mode == "off":
        return {"mode": "off", "computed": False}

    recommendation = recommend_effort_rounds(spec, config)
    if pace is not None:
        recommendation = plan_quota_aware_effort_rounds(
            recommendation, pace, config)
    return {
        "mode": mode,
        "computed": True,
        "mode_source": decision["mode_source"],
        "explicit_fields": _json_safe(decision["explicit_fields"]),
        "fields": _json_safe(decision["fields"]),
        "recommendation": _json_safe(recommendation),
    }


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


_EFFORT_ORDER = ("low", "medium", "high")


def plan_quota_aware_effort_rounds(
    recommendation: Mapping[str, Any],
    pace: Mapping[str, Any] | None,
    config: Any = None,
) -> dict[str, Any]:
    """Apply one already-computed daily-pace snapshot to an S2 recommendation.

    This is deliberately a pure decision function: it does not probe usage or
    backend availability.  ``pace`` is the snapshot produced by the caller
    (for example, ``guiserver.build_state``), and ``recommendation`` is the
    unchanged S2 result used as the decision input.
    """
    result = dict(recommendation) if isinstance(recommendation, Mapping) else {}
    original_rounds = result.get("rounds")
    original_effort = result.get("effort")
    result["original_rounds"] = original_rounds
    result["original_effort"] = original_effort
    result["quota_adjustment"] = "none"
    result["quota_reason"] = "daily_pace 비활성 또는 측정 없음"
    result["budget_warning"] = False

    if not isinstance(pace, Mapping) or not pace.get("level"):
        return result

    level = pace.get("level")
    if level == "ok":
        result["quota_reason"] = "daily_pace 정상"
        return result

    options = _automation_options(config)
    min_rounds = options.get("min_rounds", AUTOMATION_OPTION_DEFAULTS["min_rounds"])
    if isinstance(min_rounds, bool) or not isinstance(min_rounds, int) or min_rounds < 0:
        min_rounds = AUTOMATION_OPTION_DEFAULTS["min_rounds"]
    allow_effort = options.get("allow_effort_adjustment",
                               AUTOMATION_OPTION_DEFAULTS["allow_effort_adjustment"])
    allow_effort = allow_effort is True

    if level == "stop":
        result["quota_adjustment"] = "backend_stop"
        if options.get("allow_backend_reallocation",
                       AUTOMATION_OPTION_DEFAULTS["allow_backend_reallocation"]) is True:
            result["quota_reason"] = "backend 정지, 대체 backend 필요"
            result["backend_reallocation_required"] = True
        else:
            result["quota_reason"] = "backend 정지, 재배정 비허용"
            result["backend_reallocation_required"] = False
        result["budget_warning"] = True
        return result

    # Warn, and an explicit cap overrun, use the same conservative path.  Do
    # not infer a cost from the snapshot: actual cost prediction belongs to a
    # later planner and must not turn this pure function into a usage probe.
    over_cap = False
    try:
        over_cap = float(pace.get("used")) >= float(pace.get("cap"))
    except (TypeError, ValueError):
        pass
    if level != "warn" and not over_cap:
        result["quota_reason"] = f"알 수 없는 daily_pace level={level!r}; S2 추천 유지"
        return result

    candidates = result.get("round_candidates", ())
    try:
        candidate_floor = min(int(value) for value in candidates)
    except (TypeError, ValueError):
        candidate_floor = int(original_rounds) if isinstance(original_rounds, int) else min_rounds
    floor = max(min_rounds, candidate_floor)
    try:
        current_rounds = int(original_rounds)
    except (TypeError, ValueError):
        current_rounds = floor
    adjusted_rounds = floor if current_rounds > floor else current_rounds
    changed_rounds = adjusted_rounds < current_rounds
    if changed_rounds:
        result["rounds"] = adjusted_rounds

    changed_effort = False
    # A cap overrun is already evidence that the reduced-round plan is still
    # too expensive; otherwise effort is the second step only when rounds are
    # already at their candidate floor.
    if (not changed_rounds or over_cap) and allow_effort:
        effort_candidates = result.get("effort_candidates", ())
        allowed = [value for value in _EFFORT_ORDER if value in effort_candidates]
        if original_effort in allowed:
            index = allowed.index(original_effort)
            if index > 0:
                result["effort"] = allowed[index - 1]
                changed_effort = True

    if changed_rounds and changed_effort:
        result["quota_adjustment"] = "rounds_and_effort"
    elif changed_rounds:
        result["quota_adjustment"] = "rounds"
    elif changed_effort:
        result["quota_adjustment"] = "effort"

    result["budget_warning"] = over_cap or (not changed_rounds and not changed_effort)
    if result["budget_warning"]:
        result["quota_reason"] = "예산 초과 예상"
    elif changed_effort:
        result["quota_reason"] = "quota 경고로 rounds 하한 적용 후 effort 한 단계 하향"
    else:
        result["quota_reason"] = "quota 경고로 rounds를 후보 하한까지 조정"
    return result


# Short aliases make the S4 decision layer easy to discover for callers.
quota_aware_plan = plan_quota_aware_effort_rounds
recommend_quota_aware_plan = plan_quota_aware_effort_rounds
quota_aware_recommendation = plan_quota_aware_effort_rounds


# Descriptive aliases keep the public API discoverable without duplicating logic.
compute_task_features = calculate_task_features
recommend_automation = recommend_effort_rounds
