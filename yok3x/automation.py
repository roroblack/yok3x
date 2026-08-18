"""S1 automation-mode contract and precedence decisions."""
from __future__ import annotations

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
