"""Automation-mode contracts and deterministic S2 task recommendations."""
from __future__ import annotations

from pathlib import Path
import re
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
    roles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the explainable, display-only automation decision snapshot."""
    decision = effective_automation_decision(spec, config)
    mode = decision["effective_mode"]
    if mode == "off":
        return {"mode": "off", "computed": False}

    recommendation = recommend_effort_rounds(spec, config)
    role_inputs = roles if isinstance(roles, Mapping) else {}

    def role_pace(role: str) -> Mapping[str, Any] | None:
        if not isinstance(pace, Mapping):
            return None
        # Backward-compatible pure-function seam: existing callers may pass
        # one pace object rather than the production backend-keyed snapshot.
        if "level" in pace:
            return pace
        role_input = role_inputs.get(role)
        backend = role_input.get("backend") if isinstance(role_input, Mapping) else None
        value = pace.get(backend) if backend else None
        return value if isinstance(value, Mapping) else None

    producer_pace = role_pace("producer")
    quota_recommendation = plan_quota_aware_effort_rounds(
        recommendation, producer_pace, config) if pace is not None else dict(recommendation)
    role_decisions: dict[str, Any] = {}
    protected_workers = (((_config_mapping(config).get("guard") or {}).get("degrade") or {})
                         .get("roles_no_downgrade") or [])
    for role in ("producer", "reviewer"):
        raw = role_inputs.get(role)
        if not isinstance(raw, Mapping):
            continue
        current_effort = raw.get("effort") or recommendation.get("effort")
        source = dict(recommendation)
        source["effort"] = current_effort
        candidate = plan_quota_aware_effort_rounds(source, role_pace(role), config)
        protected = role == "reviewer" and raw.get("worker") in protected_workers
        candidate_effort = candidate.get("effort")
        suppressed = protected and candidate_effort != current_effort
        if suppressed:
            candidate_effort = current_effort
        current = {
            "worker": raw.get("worker"), "backend": raw.get("backend"),
            "model": raw.get("model"), "effort": current_effort,
        }
        role_decisions[role] = {
            "current": current,
            "quota_candidate": {
                "worker": current["worker"], "backend": current["backend"],
                "model": current["model"], "effort": candidate_effort,
                "quota_adjustment": candidate.get("quota_adjustment", "none"),
                "reason": "pace 경고에 따른 보수적 후보(관측 전용, 미적용)",
            },
            "roles_no_downgrade_applied": protected,
            "effort_change_suppressed": suppressed,
        }
    snapshot = {
        "mode": mode,
        "computed": True,
        "mode_source": decision["mode_source"],
        "explicit_fields": _json_safe(decision["explicit_fields"]),
        "fields": _json_safe(decision["fields"]),
        "recommendation": _json_safe(recommendation),
        "quota_recommendation": _json_safe(quota_recommendation),
        "quota_adjustment": quota_recommendation.get("quota_adjustment", "none"),
        "pace": _json_safe(pace) if pace is not None else None,
        "role_decisions": _json_safe(role_decisions),
        "quota_observation_only": True,
    }
    return snapshot


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
    def risk_match(term: str) -> bool:
        # Risk terms are tokens, not arbitrary substrings (e.g. preview must
        # not match review). Keep the historical deserializ stem.
        suffix = r"\w*" if term == "deserializ" else ""
        return re.search(
            rf"(?<!\w){re.escape(term.casefold())}{suffix}(?!\w)", searchable
        ) is not None

    risk_terms = tuple(term for term in _RISK_TERMS if risk_match(term))
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
    if level not in ("warn", "stop"):
        result["quota_reason"] = f"unknown daily_pace level={level!r}; S2 recommendation preserved"
        return result
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


def _calibration_groups_for_candidate(
    candidate: Any, calibration_stats: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Return S3 groups whose backend/model identity matches ``candidate``.

    S3 currently groups by backend (and may not have model information), while
    callers can use a logical model name.  Accept the explicit model-like
    fields when present and also match the backend component of ``backend/model``
    names.  No group is selected by score, so this helper cannot change a
    candidate's availability or ordering.
    """
    if not isinstance(calibration_stats, Mapping):
        return []
    groups = calibration_stats.get("groups", [])
    if not isinstance(groups, (list, tuple)):
        return []
    name = str(candidate).casefold()
    tokens = {part for part in name.replace("@", "/").replace(":", "/").split("/") if part}
    matched = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        identities = []
        for field in ("candidate", "logical_model", "model", "backend", "reviewer"):
            value = group.get(field)
            if isinstance(value, str) and value:
                identities.extend((value.casefold(), *value.casefold().replace("@", "/").replace(":", "/").split("/")))
        if name in identities or tokens.intersection(identities):
            matched.append(group)
    return matched


def _calibration_summary(candidate: Any, calibration_stats: Mapping[str, Any] | None) -> dict[str, Any] | None:
    groups = _calibration_groups_for_candidate(candidate, calibration_stats)
    usable = []
    for group in groups:
        count = group.get("sample_count")
        score = group.get("moving_average_score", group.get("average_score", group.get("mean_score")))
        if (isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0
                and isinstance(score, (int, float)) and not isinstance(score, bool)):
            usable.append((int(count), float(score), group.get("confidence")))
    if not usable:
        return None
    total = sum(count for count, _, _ in usable)
    average_score = sum(count * score for count, score, _ in usable) / total
    confidence_values = [float(conf) for _, _, conf in usable
                         if isinstance(conf, (int, float)) and not isinstance(conf, bool)]
    confidence = (sum(count * conf for (count, _, _), conf in zip(usable, confidence_values)) / total
                  if confidence_values and len(confidence_values) == len(usable) else None)
    return {"sample_count": total, "calibration_score": average_score, "confidence": confidence}


def calibrated_benchmark_scores(
    benchmarks: dict, calibration_stats: dict, *, min_samples: int = 5,
) -> dict[str, dict[str, Any]]:
    """Return explainable benchmark corrections without changing routing.

    The input mapping's order is deliberately retained.  Thus equal corrected
    scores remain in the original deterministic order; this function never
    introduces a tie-break based on iteration timing or random exploration.
    """
    threshold = min_samples if isinstance(min_samples, int) and not isinstance(min_samples, bool) else 5
    threshold = max(0, threshold)
    result = {}
    source = benchmarks if isinstance(benchmarks, Mapping) else {}
    for candidate, value in source.items():
        try:
            original = float(value)
        except (TypeError, ValueError):
            result[str(candidate)] = {"original": value, "corrected": value, "applied": False,
                                      "reason": "benchmark score is not numeric", "sample_count": 0}
            continue
        summary = _calibration_summary(candidate, calibration_stats)
        if summary is None:
            result[candidate] = {"original": original, "corrected": original, "applied": False,
                                 "reason": "matching calibration data unavailable", "sample_count": 0}
            continue
        count = summary["sample_count"]
        if count < threshold:
            result[candidate] = {"original": original, "corrected": original, "applied": False,
                                 "reason": f"calibration samples below min_samples={threshold}",
                                 "sample_count": count}
            continue
        weight = min(0.35, 0.10 + 0.25 * count / (count + threshold + 1))
        corrected = original * (1.0 - weight) + summary["calibration_score"] * weight
        result[candidate] = {"original": original, "corrected": corrected, "applied": True,
                             "reason": f"calibration average blended with weight={weight:.3f}",
                             "sample_count": count}
    return result


def record_experiment_comparison(
    candidates: list[str], calibration_stats: dict, *, experiment_budget: int = 0,
) -> dict[str, Any]:
    """Record comparisons from existing calibration only when explicitly approved."""
    if experiment_budget == 0:
        return {"recorded": False, "reason": "experiment_budget=0(미승인)"}
    if not isinstance(experiment_budget, int) or experiment_budget < 0:
        return {"recorded": False, "reason": "experiment_budget<0(무효)"}
    observations = []
    for candidate in candidates if isinstance(candidates, list) else []:
        summary = _calibration_summary(candidate, calibration_stats)
        if summary is not None:
            observations.append({"candidate": candidate, **summary})
    ranked = sorted(enumerate(observations), key=lambda item: (
        -item[1]["calibration_score"], -item[1]["sample_count"], item[0]))
    ranking = [item[1] for item in ranked]
    comparisons = []
    if ranking:
        best = ranking[0]
        for other in ranking[1:]:
            comparisons.append({"winner": best["candidate"] if best["calibration_score"] >= other["calibration_score"] else other["candidate"],
                                "candidate_a": best["candidate"], "candidate_b": other["candidate"]})
    return {"recorded": True, "reason": "experiment_budget>0(승인)",
            "experiment_budget": experiment_budget, "candidates": ranking,
            "best_candidate": ranking[0]["candidate"] if ranking else None,
            "comparisons": comparisons}


# Short aliases make the S4 decision layer easy to discover for callers.
quota_aware_plan = plan_quota_aware_effort_rounds
recommend_quota_aware_plan = plan_quota_aware_effort_rounds
quota_aware_recommendation = plan_quota_aware_effort_rounds


# Descriptive aliases keep the public API discoverable without duplicating logic.
compute_task_features = calculate_task_features
recommend_automation = recommend_effort_rounds
