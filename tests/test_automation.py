import json
import shutil
import time
import uuid
from pathlib import Path

import pytest

from yok3x.automation import (
    EXPLICIT_TASK_FIELDS,
    build_automation_decision_snapshot,
    calibrated_benchmark_scores,
    calculate_task_features,
    effective_automation_decision,
    plan_quota_aware_effort_rounds,
    recommend_effort_rounds,
    record_experiment_comparison,
    resolve_effective_mode,
    validate_automation_config,
    validate_automation_mode,
    validate_task_automation_mode,
)
from yok3x.config import Config, scaffold
from yok3x.orchestrator import Orchestrator, RunAborted, _resume_supported, run_task_file
from yok3x.calibration import load_calibration_statistics
from yok3x import limits, usage


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


@pytest.mark.parametrize("pattern", ["pipeline", "fanout", "fanout-fanin"])
@pytest.mark.parametrize("mode", ["off", "assist", "full"])
def test_automation_modes_are_compatible_with_non_review_patterns(
    mock_root, monkeypatch, pattern, mode
):
    """S9: pipeline/fanout 경로는 PR 전용 필드를 받아도 조용히 무시한다.

    모든 worker 실행은 mock 메서드로 대체한다. 특히 full의 applied max_rounds/pass_score가
    비-PR 패턴의 호출 시그니처로 새어 나가지 않는지 확인한다.
    """
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = mode
    calls = []

    def fake_pipeline(self, task, stages, initial_context=""):
        calls.append(("pipeline", task, stages, initial_context))
        self._save_status("done")

    def fake_fanout(self, task, workers, join_worker=None, initial_context=""):
        calls.append(("fanout", task, workers, join_worker, initial_context))
        self._save_status("done")

    monkeypatch.setattr(Orchestrator, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(Orchestrator, "run_fanout", fake_fanout)
    spec = {"pattern": pattern, "task": f"{pattern} task"}
    if pattern == "pipeline":
        spec["stages"] = [{"worker": "claude-main"}, {"worker": "codex-main"}]
    else:
        spec["workers"] = ["claude-main", "codex-main"]
        spec["join_worker"] = "codex-critic"
    task_file = mock_root / f"{pattern}-{mode}.json"
    task_file.write_text(json.dumps(spec), encoding="utf-8")

    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert calls and calls[0][0] == ("pipeline" if pattern == "pipeline" else "fanout")
    status = json.loads(next(cfg.paths.runs.glob("run_*/status.json")).read_text(encoding="utf-8"))
    decision = status["automation_decision"]
    if mode == "full":
        assert set(decision["applied"]) == {"max_rounds", "pass_score"}
    else:
        assert "applied" not in decision


def test_full_mode_cannot_bypass_guard_stop_before_worker_call(mock_root, monkeypatch):
    """S9 안전장치 4: full 자동화보다 기존 hard-stop/preflight가 먼저 최종 결정한다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["automation_mode"] = "full"
    task_file = mock_root / "guard-stop.json"
    task_file.write_text(json.dumps({
        "pattern": "pipeline", "task": "must stop",
        "stages": [{"worker": "claude-main"}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        Orchestrator, "preflight_budget",
        lambda self, spec: (_ for _ in ()).throw(
            RunAborted("guard hard stop", cause="guard_stop")),
    )
    monkeypatch.setattr(
        Orchestrator, "run_pipeline",
        lambda *args, **kwargs: pytest.fail("guard stop 뒤에는 worker 경로를 실행하면 안 됨"),
    )

    result = run_task_file(cfg, task_file, auto=True)
    assert result.startswith("aborted: guard hard stop")
    status = json.loads(next(cfg.paths.runs.glob("run_*/status.json")).read_text(encoding="utf-8"))
    assert status["cause"] == "guard_stop"
    assert status["automation_decision"]["applied"] == {"max_rounds": {"from": None, "to": 2},
                                                        "pass_score": {"from": None, "to": 8.0}}


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


@pytest.mark.parametrize("automation_mode", ["Full", [], {"mode": "full"}, 1])
def test_resume_rejects_malformed_automation_mode_without_raising(mock_root, automation_mode):
    ok, reason = _resume_supported(
        {"pattern": "producer-reviewer", "task": "x", "automation_mode": automation_mode},
        Config.load(mock_root),
    )
    assert ok is False
    assert reason


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


@pytest.mark.parametrize("value", [None, "", "\U0001f680" * 1000, "{" * 1000])
def test_s2_feature_extraction_handles_empty_unicode_and_structured_text(value):
    features = calculate_task_features({"task": value})
    assert isinstance(features["input_chars"], int)
    assert features["input_chars"] <= 400_000


def test_s2_risk_terms_do_not_match_inside_other_words():
    assert calculate_task_features({"task": "preview the result"})["risk"] is False


def test_snapshot_off_ignores_malformed_pace(monkeypatch):
    monkeypatch.setattr(
        "yok3x.automation.recommend_effort_rounds",
        lambda *args, **kwargs: pytest.fail("off mode must return before recommendation"),
    )
    assert build_automation_decision_snapshot(
        {"task": "x"}, {"automation_mode": "off"},
        pace={"level": ["unknown"], "used": None, "cap": object()},
    ) == {"mode": "off", "computed": False}


def test_s4_unknown_pace_level_does_not_adjust_even_when_numbers_exceed_cap():
    source = {"effort": "high", "rounds": 4, "effort_candidates": ("medium", "high"),
              "round_candidates": (2, 4)}
    result = plan_quota_aware_effort_rounds(
        source, {"level": "future", "used": "999", "cap": 1},
    )
    assert result["rounds"] == source["rounds"]
    assert result["effort"] == source["effort"]
    assert result["quota_adjustment"] == "none"


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


def _s7_stats(*groups):
    return {"groups": [
        {"backend": candidate, "sample_count": count,
         "moving_average_score": score, "confidence": confidence}
        for candidate, count, score, confidence in groups
    ]}


def test_s7_calibrates_only_candidates_with_enough_samples():
    result = calibrated_benchmark_scores(
        {"claude": 8.0, "codex": 7.0},
        _s7_stats(("claude", 5, 9.0, 1.0), ("codex", 4, 10.0, 0.8)),
        min_samples=5,
    )
    assert result["claude"]["applied"] is True
    assert result["claude"]["corrected"] > 8.0
    assert result["codex"]["applied"] is False
    assert result["codex"]["corrected"] == 7.0
    assert result["codex"]["sample_count"] == 4


def test_s7_missing_or_empty_calibration_preserves_benchmarks():
    benchmarks = {"known": 8, "missing": 6}
    assert calibrated_benchmark_scores(benchmarks, {"groups": []})["known"]["corrected"] == 8.0
    assert calibrated_benchmark_scores(benchmarks, {})["missing"]["corrected"] == 6.0
    assert calibrated_benchmark_scores(benchmarks, _s7_stats(("known", 0, 10, 1.0)))["known"]["applied"] is False


def test_s7_candidate_availability_and_ties_are_deterministic():
    result = calibrated_benchmark_scores(
        {"first": 8.0, "second": 8.0, "unavailable": 9.0},
        _s7_stats(("first", 5, 8.0, 1.0), ("second", 5, 8.0, 1.0)),
    )
    assert list(result) == ["first", "second", "unavailable"]
    assert result["unavailable"]["applied"] is False
    assert result["first"]["corrected"] == result["second"]["corrected"]


def test_s7_experiment_budget_zero_does_not_record():
    assert record_experiment_comparison(["claude"], _s7_stats(("claude", 5, 9, 1.0))) == {
        "recorded": False, "reason": "experiment_budget=0(미승인)"
    }


def test_s7_experiment_budget_records_existing_calibration_only():
    result = record_experiment_comparison(
        ["codex", "missing", "claude"],
        _s7_stats(("codex", 5, 7.0, 0.8), ("claude", 6, 9.0, 1.0)),
        experiment_budget=1,
    )
    assert result["recorded"] is True
    assert result["best_candidate"] == "claude"
    assert [row["candidate"] for row in result["candidates"]] == ["claude", "codex"]
    assert all(row["candidate"] != "missing" for row in result["candidates"])
    assert result["candidates"][0]["sample_count"] == 6
    assert result["candidates"][0]["confidence"] == 1.0


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


@pytest.fixture
def quota_scenario(tmp_path):
    root = tmp_path / "quota-scenario"
    scaffold(root, use_mock=True)
    cfg = Config.load(root)
    cfg.yok3x["guard"]["daily_pace"].update({
        "enabled": True, "strategy": "catch_up", "mode": "warn",
        "pct_of_weekly": 0.14, "soft_frac": 0.8,
    })
    cfg.yok3x["automation_mode"] = "full"
    cfg.yok3x["automation"].update({
        "allow_effort_adjustment": True,
        "allow_backend_reallocation": True,
    })
    now = 1_800_000_000.0
    reset_at = now + 4 * 86400.0
    reading = limits.LimitReading(
        "codex", "codex_appserver", ok=True, real=True,
        windows=[limits.Window("7d", 70.0, resets_at=reset_at)],
    )
    return cfg, now, reset_at, reading


@pytest.mark.parametrize("mode,expected_level", [("warn", "warn"), ("pause", "stop")])
def test_quota_scenario_separates_reset_pace_mode_and_codex_confidence(
    quota_scenario, monkeypatch, mode, expected_level
):
    cfg, now, reset_at, reading = quota_scenario
    cfg.yok3x["guard"]["daily_pace"]["mode"] = mode
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr("yok3x.usage.time.time", lambda: now)
    monkeypatch.setattr("yok3x.limits.time.time", lambda: now)
    monkeypatch.setattr("yok3x.limits.codex_percent_at", lambda *_args, **_kwargs: None)

    snapshot = usage.automation_pace_snapshot(
        cfg, ["codex"], probe_fn=lambda _cfg, _backend: reading)["codex"]

    assert snapshot["reset_at"] - now == 4 * 86400.0
    assert snapshot["current"] == 70.0
    assert snapshot["since_reset_known"] is False
    assert snapshot["daily_pace_status"] == "measured"
    assert snapshot["level"] == expected_level
    assert snapshot["cap"] == 0.0


def test_automation_pace_snapshot_distinguishes_off_unknown_and_estimated(tmp_path):
    root = tmp_path / "pace-states"
    scaffold(root, use_mock=True)
    cfg = Config.load(root)

    off = usage.automation_pace_snapshot(
        cfg, ["codex"], probe_fn=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    assert off["codex"]["daily_pace_status"] == "off"
    assert off["codex"]["measurement"] == "unknown"

    cfg.yok3x["guard"]["daily_pace"]["enabled"] = True
    failed = limits.LimitReading(
        "codex", "codex_appserver", ok=False, real=False, error="probe failed")
    unknown = usage.automation_pace_snapshot(cfg, ["codex"], probe_fn=lambda *_: failed)
    assert unknown["codex"]["daily_pace_status"] == "unknown"
    assert unknown["codex"]["reason"] == "measurement_failed"

    estimated = limits.LimitReading(
        "codex", "codex_sessions", ok=True, real=False,
        windows=[limits.Window("7d", 70.0)])
    estimate = usage.automation_pace_snapshot(cfg, ["codex"], probe_fn=lambda *_: estimated)
    assert estimate["codex"]["measurement"] == "estimated"
    assert estimate["codex"]["daily_pace_status"] == "unknown"
    assert estimate["codex"]["level"] == "unknown"


def test_full_quota_assist_wiring_never_changes_execution_values(
    quota_scenario, monkeypatch, tmp_path
):
    cfg, _now, reset_at, _reading = quota_scenario
    cfg.yok3x["workers"]["codex-main"]["backend"] = "codex"
    cfg.yok3x["workers"]["codex-critic"]["backend"] = "codex"
    pace = {
        "codex": {
            "backend": "codex", "daily_pace_status": "measured",
            "measurement": "measured", "source": "codex_appserver", "real": True,
            "current": 70.0, "reset_at": reset_at, "reset": "4일 후",
            "since_reset_known": False, "used": 0.0, "cap": 0.0,
            "level": "warn", "mode": "warn",
        }
    }
    monkeypatch.setattr("yok3x.orchestrator.usage.automation_pace_snapshot",
                        lambda *_args, **_kwargs: pace)
    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured.update({
            "max_rounds": max_rounds, "pass_score": pass_score,
            "producer_effort": self._worker(producer).get("effort"),
            "reviewer_effort": self._worker(reviewer).get("effort"),
        })
        self._save_status("done")

    monkeypatch.setattr(Orchestrator, "run_producer_reviewer", fake_run)
    task_file = tmp_path / "risk-task.json"
    task_file.write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "security migration review",
        "producer": "codex-main", "reviewer": "codex-critic",
    }), encoding="utf-8")

    assert run_task_file(cfg, task_file, auto=True) == "done"
    assert captured == {
        "max_rounds": 4, "pass_score": 8.0,
        "producer_effort": "high", "reviewer_effort": "high",
    }
    status = json.loads(next(cfg.paths.runs.glob("run_*/status.json")).read_text(encoding="utf-8"))
    decision = status["automation_decision"]
    assert decision["recommendation"]["rounds"] == 4
    assert decision["recommendation"]["effort"] == "high"
    assert decision["quota_recommendation"]["rounds"] == 3
    assert decision["quota_recommendation"]["effort"] == "medium"
    assert decision["quota_adjustment"] == "rounds_and_effort"
    assert decision["quota_observation_only"] is True
    assert decision["role_decisions"]["producer"]["quota_candidate"]["effort"] == "medium"
    reviewer = decision["role_decisions"]["reviewer"]
    assert reviewer["current"]["effort"] == "high"
    assert reviewer["quota_candidate"]["effort"] == "high"
    assert reviewer["roles_no_downgrade_applied"] is True
    assert reviewer["effort_change_suppressed"] is True
