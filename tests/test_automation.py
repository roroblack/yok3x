import json
import shutil
import uuid
from pathlib import Path

import pytest

from yok3x.automation import (
    EXPLICIT_TASK_FIELDS,
    build_automation_decision_snapshot,
    calculate_task_features,
    effective_automation_decision,
    plan_quota_aware_effort_rounds,
    recommend_effort_rounds,
    resolve_effective_mode,
    validate_automation_config,
    validate_automation_mode,
    validate_task_automation_mode,
)
from yok3x.config import Config, scaffold
from yok3x.orchestrator import _resume_supported, run_task_file
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


def test_snapshot_off_does_not_calculate_recommendation(monkeypatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("recommendation must not be calculated in off mode")

    monkeypatch.setattr("yok3x.automation.recommend_effort_rounds", fail_if_called)
    assert build_automation_decision_snapshot({"task": "x"}, {}) == {
        "mode": "off", "computed": False}
    assert called is False


def test_snapshot_assist_is_json_safe_and_preserves_skip_reasons(monkeypatch):
    monkeypatch.setattr(
        "yok3x.automation.recommend_effort_rounds",
        lambda spec, config: {
            "bucket": "tiny", "effort": "low", "rounds": 1,
            "round_candidates": (1, 2), "reason": "sample unavailable",
        },
    )
    snapshot = build_automation_decision_snapshot(
        {"task": "x", "producer": "explicit-worker"},
        {"automation_mode": "assist"},
    )
    assert snapshot["mode"] == "assist"
    assert snapshot["computed"] is True
    assert snapshot["explicit_fields"] == ["producer"]
    assert snapshot["fields"]["producer"]["protected"] is True
    assert snapshot["recommendation"]["round_candidates"] == [1, 2]
    assert snapshot["recommendation"]["reason"] == "sample unavailable"


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


def test_assist_mode_only_adds_snapshot_to_status(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = "assist"
    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured.update(producer=producer, reviewer=reviewer,
                        max_rounds=max_rounds, pass_score=pass_score)
        self._save_status("done")

    monkeypatch.setattr("yok3x.orchestrator.Orchestrator.run_producer_reviewer", fake_run)
    task_file = mock_root / "task.json"
    task_file.write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "assist display",
        "producer": "codex-main", "max_rounds": 3,
    }), encoding="utf-8")

    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert captured == {
        "producer": "codex-main", "reviewer": "codex-critic",
        "max_rounds": 3, "pass_score": 8.0,
    }
    status_files = list(cfg.paths.runs.glob("run_*/status.json"))
    assert len(status_files) == 1
    status = json.loads(status_files[0].read_text(encoding="utf-8"))
    snapshot = status["automation_decision"]
    assert snapshot["mode"] == "assist"
    assert snapshot["computed"] is True
    assert snapshot["fields"]["producer"]["protected"] is True
    assert isinstance(snapshot["recommendation"]["round_candidates"], list)


def test_full_mode_fills_only_absent_round_fields_and_preserves_source(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = "full"
    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured.update(max_rounds=max_rounds, pass_score=pass_score)
        self._save_status("done")

    monkeypatch.setattr("yok3x.orchestrator.Orchestrator.run_producer_reviewer", fake_run)
    source = {"pattern": "producer-reviewer", "task": "full", "max_rounds": 0,
              "pass_score": 0}
    task_file = mock_root / "task.json"
    task_file.write_text(json.dumps(source), encoding="utf-8")

    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert captured == {"max_rounds": 0, "pass_score": 0.0}
    assert json.loads(task_file.read_text(encoding="utf-8")) == source
    status = json.loads(next(cfg.paths.runs.glob("run_*/status.json")).read_text(encoding="utf-8"))
    decision = status["automation_decision"]
    assert decision["applied"] == {}
    assert decision["effective"]["max_rounds"] == 0
    assert decision["effective"]["pass_score"] == 0


def test_full_mode_applies_missing_round_fields_once_and_is_sticky(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = "full"
    calls = []

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        calls.append((max_rounds, pass_score))
        self._save_status("done")

    monkeypatch.setattr("yok3x.orchestrator.Orchestrator.run_producer_reviewer", fake_run)
    task_file = mock_root / "task.json"
    task_file.write_text(json.dumps({"pattern": "producer-reviewer", "task": "x"}), encoding="utf-8")
    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert calls == [(1, 8.0)]


@pytest.mark.parametrize("allow_effort, expected", [(False, None), (True, "low")])
def test_full_mode_effort_requires_explicit_opt_in(mock_root, monkeypatch, allow_effort, expected):
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = "full"
    cfg.yok3x["automation"]["allow_effort_adjustment"] = allow_effort
    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured["effort"] = self._worker(producer).get("effort")
        self._save_status("done")

    monkeypatch.setattr("yok3x.orchestrator.Orchestrator.run_producer_reviewer", fake_run)
    task_file = mock_root / "task.json"
    task_file.write_text(json.dumps({"pattern": "producer-reviewer", "task": "x"}), encoding="utf-8")
    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert captured["effort"] == expected


def test_full_mode_resume_is_rejected_with_reason(mock_root):
    cfg = Config.load(mock_root)
    ok, reason = _resume_supported(
        {"pattern": "producer-reviewer", "task": "x", "automation_mode": "full"}, cfg)
    assert ok is False
    assert "automation_mode=full" in reason


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


def _pace(level, *, used=10.0, cap=20.0):
    return {"used": used, "cap": cap, "soft": 16.0, "base_cap": 20.0,
            "strategy": "fixed", "start": 0.0, "forward_daily": None,
            "even_cap": None, "level": level, "blocked": level == "stop",
            "approved": False}


def _quota_recommendation(*, rounds=4, effort="high"):
    return {"bucket": "large", "effort": effort, "rounds": rounds,
            "effort_candidates": ("medium", "high"),
            "round_candidates": (2, 4), "confidence": 0.8, "reason": "s2"}


def test_s4_none_and_ok_pass_s2_through_with_observability():
    source = _quota_recommendation()
    for pace in (None, _pace("ok")):
        result = plan_quota_aware_effort_rounds(source, pace)
        assert result["rounds"] == 4 and result["effort"] == "high"
        assert result["quota_adjustment"] == "none"
        assert result["original_rounds"] == 4
        assert result["original_effort"] == "high"
    assert plan_quota_aware_effort_rounds(source, {})["quota_reason"] == "daily_pace 비활성 또는 측정 없음"


def test_s4_warn_reduces_rounds_to_candidate_floor_and_honors_min_rounds():
    result = plan_quota_aware_effort_rounds(
        _quota_recommendation(), _pace("warn"),
        {"automation": {"min_rounds": 3}},
    )
    assert result["rounds"] == 3
    assert result["effort"] == "high"
    assert result["quota_adjustment"] == "rounds"
    assert result["budget_warning"] is False


def test_s4_warn_at_round_floor_can_lower_effort_only_when_enabled():
    source = _quota_recommendation(rounds=2)
    result = plan_quota_aware_effort_rounds(
        source, _pace("warn"),
        {"automation": {"allow_effort_adjustment": True}},
    )
    assert result["rounds"] == 2 and result["effort"] == "medium"
    assert result["quota_adjustment"] == "effort"
    assert result["budget_warning"] is False


def test_s4_overuse_can_reduce_rounds_and_effort_and_warn():
    result = plan_quota_aware_effort_rounds(
        _quota_recommendation(), _pace("warn", used=21.0, cap=20.0),
        {"automation": {"allow_effort_adjustment": True}},
    )
    assert result["rounds"] == 2 and result["effort"] == "medium"
    assert result["quota_adjustment"] == "rounds_and_effort"
    assert result["budget_warning"] is True
    assert result["original_rounds"] == 4 and result["original_effort"] == "high"


def test_s4_lowest_effort_is_never_lowered_outside_candidates():
    result = plan_quota_aware_effort_rounds(
        _quota_recommendation(rounds=2, effort="medium"), _pace("warn", used=21, cap=20),
        {"automation": {"allow_effort_adjustment": True}},
    )
    assert result["effort"] == "medium"
    assert result["budget_warning"] is True
    assert result["quota_reason"] == "예산 초과 예상"


@pytest.mark.parametrize("allow, expected, signal", [
    (False, False, "backend 정지, 재배정 비허용"),
    (True, True, "backend 정지, 대체 backend 필요"),
])
def test_s4_stop_only_emits_optional_backend_reallocation_signal(allow, expected, signal):
    result = plan_quota_aware_effort_rounds(
        _quota_recommendation(), _pace("stop"),
        {"automation": {"allow_backend_reallocation": allow}},
    )
    assert result["quota_adjustment"] == "backend_stop"
    assert result["backend_reallocation_required"] is expected
    assert result["quota_reason"] == signal
    assert result["rounds"] == 4 and result["effort"] == "high"
