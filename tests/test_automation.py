import shutil
import uuid
from pathlib import Path

import pytest

from yok3x.automation import (
    EXPLICIT_TASK_FIELDS,
    calculate_task_features,
    effective_automation_decision,
    recommend_effort_rounds,
    resolve_effective_mode,
    validate_automation_config,
    validate_automation_mode,
    validate_task_automation_mode,
)
from yok3x.config import Config, scaffold
from yok3x.orchestrator import run_task_file
from yok3x.calibration import load_calibration_statistics


@pytest.fixture
def mock_root():
    root = Path(".pytest-temp") / f"automation-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    scaffold(root, use_mock=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("mode", ["off", "assist", "full"])
def test_validate_automation_mode_accepts_supported_values(mode):
    assert validate_automation_mode(mode) == mode


@pytest.mark.parametrize("value", ["turbo", None, 123, True])
def test_validate_automation_mode_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_automation_mode(value)


def test_validate_automation_config_accepts_defaults_and_missing_mode():
    validate_automation_config({})
    validate_automation_config({"automation": {}})
    validate_automation_config({"automation": {"max_rounds_cap": 0}})


@pytest.mark.parametrize(
    "automation",
    [
        {"max_rounds_cap": -1},
        {"max_rounds_cap": True},
        {"max_rounds_cap": 1.5},
        {"allow_backend_reallocation": "yes"},
    ],
)
def test_validate_automation_config_rejects_invalid_options(automation):
    with pytest.raises(ValueError):
        validate_automation_config({"automation": automation})


def test_validate_automation_config_rejects_non_mapping_automation():
    with pytest.raises(ValueError):
        validate_automation_config({"automation": []})


def test_effective_automation_decision_applies_task_global_default_precedence():
    task = effective_automation_decision(
        {"automation_mode": "full"}, {"automation_mode": "assist"}
    )
    assert task["effective_mode"] == "full"
    assert task["mode_source"] == "task"

    global_decision = effective_automation_decision({}, {"automation_mode": "assist"})
    assert global_decision["effective_mode"] == "assist"
    assert global_decision["mode_source"] == "global"

    default = effective_automation_decision()
    assert default["effective_mode"] == "off"
    assert default["mode_source"] == "default"


def test_effective_automation_decision_marks_only_explicit_fields_protected():
    decision = effective_automation_decision({"producer": "claude-main"})

    assert decision["explicit_fields"] == ("producer",)
    assert decision["fields"]["producer"] == {
        "protected": True,
        "source": "explicit",
    }
    for field in EXPLICIT_TASK_FIELDS - {"producer"}:
        assert decision["fields"][field]["protected"] is False


@pytest.mark.parametrize(
    "task_spec,config",
    [
        ({"automation_mode": "full"}, {"automation_mode": "assist"}),
        ({}, {"automation_mode": "assist"}),
        ({}, {}),
    ],
)
def test_resolve_effective_mode_matches_decision(task_spec, config):
    assert resolve_effective_mode(task_spec, config) == effective_automation_decision(
        task_spec, config
    )["effective_mode"]


def test_validate_task_automation_mode_only_validates_present_value():
    validate_task_automation_mode({"task": "no override"})
    validate_task_automation_mode({"automation_mode": "assist"})
    with pytest.raises(ValueError):
        validate_task_automation_mode({"automation_mode": "turbo"})


def test_off_mode_preserves_existing_task_defaults(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    assert cfg.yok3x["automation_mode"] == "off"

    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured.update(
            task=task,
            producer=producer,
            reviewer=reviewer,
            max_rounds=max_rounds,
            pass_score=pass_score,
        )

    monkeypatch.setattr("yok3x.orchestrator.Orchestrator.run_producer_reviewer", fake_run)
    task_file = mock_root / "task.json"
    task_file.write_text('{"pattern": "producer-reviewer", "task": "defaults"}', encoding="utf-8")

    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert captured == {
        "task": "defaults",
        "producer": "claude-main",
        "reviewer": "codex-critic",
        "max_rounds": 2,
        "pass_score": 8.0,
    }


def test_guiserver_task_spec_validation_rejects_bad_automation_mode(mock_root):
    from yok3x import guiserver as gs

    error = gs._validate_task_spec(
        {"pattern": "producer-reviewer", "task": "t", "automation_mode": "turbo"},
        Config.load(mock_root),
    )
    assert "automation_mode" in error
    assert "off|assist|full" in error


@pytest.mark.parametrize(
    "spec,expected",
    [
        ({}, "tiny"),
        ({"task": "x" * 300}, "small"),
        ({"task": "x" * 1500, "pattern": "pipeline", "stages": ["a", "b"]}, "medium"),
        ({"task": "x" * 5000}, "large"),
        ({"task": "security review of concurrent migration"}, "risk"),
    ],
)
def test_s2_recommendation_buckets(spec, expected):
    assert recommend_effort_rounds(spec)["bucket"] == expected


def test_s2_features_include_structural_signals_and_unknown_pattern_is_safe():
    spec = {
        "task": "verify this",
        "pattern": "future-pattern",
        "workers": ["a", "b"],
        "join_worker": "a",
        "acquire": {"qa": ["q1", "q2"]},
        "verify_cmd": "pytest -q",
        "materialize": {"enabled": True},
        "context_globs": ["src/*.py", "tests/*.py"],
    }
    features = calculate_task_features(spec)
    assert features["pattern_score"] == 0
    assert features["workers"] == 2
    assert features["join_worker"] == 1
    assert features["acquire_qa"] == 2
    assert features["has_verify_cmd"] is True
    assert features["has_materialize"] is True
    assert features["context_file_count"] == 2


def test_s2_recommendation_is_deterministic_and_ignores_calibration():
    spec = {"task": "pipeline task", "pattern": "pipeline", "verify_cmd": "pytest"}
    first = recommend_effort_rounds(spec, calibration={"should": "be ignored"})
    second = recommend_effort_rounds(spec, calibration=["also ignored"])
    assert first == second


@pytest.mark.parametrize("bucket_spec", [{}, {"task": "x" * 5000}, {"task": "security migration"}])
@pytest.mark.parametrize("cap", [0, 1, 2, 3, 4])
def test_s2_rounds_never_exceed_max_rounds_cap(bucket_spec, cap):
    result = recommend_effort_rounds(bucket_spec, {"automation": {"max_rounds_cap": cap}})
    assert result["rounds"] <= cap


def test_s2_does_not_lower_or_invent_pass_score():
    result = recommend_effort_rounds({"task": "security review"})
    assert "pass_score" not in result
    assert "pass_score" not in result["features"]


def _calibration_row(score, verify_ok, *, backend="claude", effort="medium", reviewer="r",
                     pattern="producer-reviewer", rounds=2, **extra):
    return {"score": score, "verify_ok": verify_ok, "backend": backend,
            "effort": effort, "reviewer": reviewer, "pattern": pattern,
            "rounds": rounds, **extra}


def _write_calibration(path, rows, malformed=()):
    import json

    lines = [json.dumps(row) for row in rows]
    for index, value in malformed:
        lines.insert(index, value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_s3_confidence_distinguishes_insufficient_and_sufficient_samples(tmp_path):
    path = tmp_path / "calibration.jsonl"
    _write_calibration(path, [_calibration_row(8 + i / 10, True) for i in range(10)])
    result = load_calibration_statistics(path)
    assert result["groups"][0]["confidence"] == 1.0
    assert result["groups"][0]["confidence_reason"] == "표본 충분"

    _write_calibration(path, [_calibration_row(8, True), _calibration_row(7, None)])
    result = load_calibration_statistics(path)
    assert result["groups"][0]["confidence"] < 1.0
    assert result["groups"][0]["confidence_reason"] == "표본 부족"


def test_s3_uses_only_recent_calibration_window(tmp_path):
    path = tmp_path / "calibration.jsonl"
    _write_calibration(path, [_calibration_row(1, False)] * 3 + [_calibration_row(9, True)] * 2)
    group = load_calibration_statistics(path, window=2)["groups"][0]
    assert group["sample_count"] == 2
    assert group["moving_average_score"] == 9.0
    assert group["success_rate"] == 1.0


def test_s3_skips_malformed_and_missing_schema_rows(tmp_path):
    path = tmp_path / "calibration.jsonl"
    rows = [_calibration_row(8, True), {"verify_ok": True}, {"score": 8},
            _calibration_row("bad", True)]
    _write_calibration(path, rows, malformed=[(1, "{not-json")])
    result = load_calibration_statistics(path)
    assert result["groups"][0]["sample_count"] == 1
    assert result["reasons"]["malformed_json"] == 1
    assert result["reasons"]["missing_score"] == 1
    assert result["reasons"]["missing_verify_ok"] == 1
    assert result["reasons"]["schema_mismatch"] == 1


def test_s3_separates_backends_and_reports_missing_bucket(tmp_path):
    path = tmp_path / "calibration.jsonl"
    _write_calibration(path, [_calibration_row(8, True, backend="claude"),
                              _calibration_row(6, False, backend="codex")])
    result = load_calibration_statistics(path)
    assert {group["backend"] for group in result["groups"]} == {"claude", "codex"}
    assert result["bucket_info"] == "bucket 정보 없음"
    assert result["model_info"] == "model 정보 없음"


def test_s3_missing_file_returns_empty_result(tmp_path):
    result = load_calibration_statistics(tmp_path / "missing.jsonl")
    assert result["groups"] == []
    assert result["records"] == []
    assert result["reasons"]["file_not_found"] == 1


def test_s3_is_reproducible_and_does_not_return_task_text(tmp_path):
    path = tmp_path / "calibration.jsonl"
    secret = "PRIVATE TASK PROMPT SHOULD NOT ESCAPE"
    _write_calibration(path, [_calibration_row(8, True, task=secret)])
    first = load_calibration_statistics(path)
    second = load_calibration_statistics(path)
    assert first == second
    assert secret not in repr(first)
