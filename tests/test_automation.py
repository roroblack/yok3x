import shutil
import uuid
from pathlib import Path

import pytest

from yok3x.automation import (
    EXPLICIT_TASK_FIELDS,
    effective_automation_decision,
    resolve_effective_mode,
    validate_automation_config,
    validate_automation_mode,
    validate_task_automation_mode,
)
from yok3x.config import Config, scaffold
from yok3x.orchestrator import run_task_file


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
