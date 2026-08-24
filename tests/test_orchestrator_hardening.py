"""orchestrator 외부 입력/재개 경계 회귀 테스트."""
from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from yok3x import orchestrator
from yok3x.backends import BackendResult
from yok3x.config import Config, scaffold


@pytest.fixture
def cfg(tmp_path):
    scaffold(tmp_path, use_mock=True)
    loaded = Config.load(tmp_path)
    loaded.yok3x["guard"]["reservation"]["preflight_enabled"] = False
    return loaded


@pytest.mark.parametrize("spec, fragment", [
    ([], "객체가 아님"),
    ({"pattern": "producer-reviewer", "task": "t", "max_rounds": -1},
     "max_rounds"),
    ({"pattern": "producer-reviewer", "task": "t", "max_rounds": None},
     "max_rounds"),
    ({"pattern": "producer-reviewer", "task": "t", "pass_score": "nan"},
     "pass_score"),
    ({"pattern": "pipeline", "task": "t", "stages": []}, "stages"),
    ({"pattern": "fanout", "task": "t", "workers": []}, "workers"),
    ({"pattern": "fanout", "task": "t", "workers": ["codex-main", "codex-main"]},
     "중복"),
    ({"pattern": "fanout", "task": "t", "workers": ["missing"]}, "없는 워커"),
    ({"pattern": "fanout", "task": "t", "workers": ["codex-main"],
      "join_worker": "missing"}, "없는 워커"),
    ({"pattern": "producer-reviewer", "task": "t",
      "materialize": {"enabled": "false"}}, "bool"),
    ({"pattern": "producer-reviewer", "task": "t",
      "changes": {"apply_mode": "unsafe"}}, "apply_mode"),
])
def test_invalid_task_specs_abort_before_backend(cfg, tmp_path, monkeypatch, spec, fragment):
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(
        orchestrator, "run_backend",
        lambda *args, **kwargs: pytest.fail("invalid spec reached backend"),
    )

    result = orchestrator.run_task_file(cfg, task_file, auto=True)

    assert isinstance(result, str) and result.startswith("aborted:")
    assert fragment in result


def test_numeric_string_round_options_remain_compatible(cfg, tmp_path, monkeypatch):
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "t",
        "max_rounds": "2", "pass_score": "8.5",
    }), encoding="utf-8")
    captured = {}

    def fake_run(self, task, producer, reviewer, max_rounds=2, pass_score=8.0, **kwargs):
        captured.update(max_rounds=max_rounds, pass_score=pass_score)

    monkeypatch.setattr(orchestrator.Orchestrator, "run_producer_reviewer", fake_run)

    assert orchestrator.run_task_file(cfg, task_file, auto=True) == "done"
    assert captured == {"max_rounds": 2, "pass_score": 8.5}


def test_legacy_score_out_of_range_is_unscored_and_cannot_pass(cfg, monkeypatch):
    orch = orchestrator.Orchestrator(cfg, auto=True)
    monkeypatch.setattr(
        orchestrator, "run_backend",
        lambda *args, **kwargs: BackendResult("mock", True, text="SCORE: 999"),
    )

    orch.call_worker("codex-critic", "review", "critic")

    assert orch.steps[-1].score is None
    assert any("SCORE 범위 오류" in item for item in orch.steps[-1].checklist)
    gate = orchestrator.evaluate_score_gate(
        "strict", has_verify_cmd=False, verify_ok=None, score=999, threshold=8)
    assert gate["passed"] is False
    assert gate["reason"] == "score_missing"


def test_resolve_model_ignores_nonfinite_scores_and_probe_exceptions(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = "best"
    cfg.yok3x["benchmarks"]["review"] = {
        "fable-5": math.nan,
        "gpt-5.6": 99.0,
    }
    expected_model = cfg.yok3x["models_catalog"]["gpt-5.6"]["model"]
    assert orchestrator.resolve_model(cfg, "critic")[:2] == ("codex", expected_model)

    cfg.yok3x["benchmarks"]["review"] = {
        "fable-5": 99.0,
        "gpt-5.6": 98.0,
    }

    def available(backend):
        if backend == "claude":
            raise RuntimeError("probe failed")
        return True

    backend, model, reason = orchestrator.resolve_model(cfg, "critic", available=available)
    assert (backend, model) == ("codex", expected_model)
    assert "폴백" in reason


def test_resume_loader_skips_parseable_step_with_bad_value_types(tmp_path):
    step = {
        "worker": "codex-main", "task_kind": "build", "task": "t",
        "call_key": "abc", "ok": True, "error": "", "text": "result",
        "score": 8.0, "checklist": [],
        "usage": {"cost_usd": "not-a-number", "total_tokens": 1, "duration_ms": 2},
    }
    (tmp_path / "step_01_codex-main.json").write_text(
        json.dumps(step), encoding="utf-8")

    cache, reason = orchestrator._load_replay_steps(tmp_path)

    assert cache == {}
    assert "usage 값 오류" in reason


def test_resume_supported_rejects_non_object_spec(cfg):
    supported, reason = orchestrator._resume_supported([], cfg)

    assert supported is False
    assert "객체" in reason


def test_materialize_no_overwrite_is_atomic_between_concurrent_runs(cfg, monkeypatch):
    root = cfg.paths.yok3x_dir.parent / "shared-output"
    root.mkdir()
    first = orchestrator.Orchestrator(cfg, auto=True)
    second = orchestrator.Orchestrator(cfg, auto=True)
    for orch in (first, second):
        orch.materialize = {
            "enabled": True, "root": str(root), "overwrite": False,
        }
    real_write = orchestrator._atomic_write_bytes
    barrier = threading.Barrier(2)

    def synchronized_write(path, data, **kwargs):
        if path.name == "same.txt":
            barrier.wait(timeout=5)
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(orchestrator, "_atomic_write_bytes", synchronized_write)
    outputs = ("```file:same.txt\nfirst\n```", "```file:same.txt\nsecond\n```")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda item: item[0]._materialize_outputs(item[1]),
            zip((first, second), outputs),
        ))

    assert sum(result["ok"] for result in results) == 1
    assert sum(bool(result["rejected"]) for result in results) == 1
    assert (root / "same.txt").read_text(encoding="utf-8") in {"first", "second"}
    assert not list(root.glob(".*.tmp"))


def test_materialize_rejects_symlinked_root(cfg, monkeypatch):
    root = cfg.paths.yok3x_dir.parent / "linked-output"
    root.mkdir()
    orch = orchestrator.Orchestrator(cfg, auto=True)
    orch.materialize = {"enabled": True, "root": str(root), "overwrite": True}
    real_is_symlink = orchestrator.Path.is_symlink
    monkeypatch.setattr(
        orchestrator.Path, "is_symlink",
        lambda path: path == root or real_is_symlink(path),
    )

    result = orch._materialize_outputs("```file:x.txt\nsecret\n```")

    assert result["ok"] is False
    assert "심볼릭" in result["reason"]
    assert not (root / "x.txt").exists()


@pytest.mark.parametrize("method, args", [
    ("run_pipeline", ("t", [])),
    ("run_fanout", ("t", [])),
    ("run_fanout", ("t", ["codex-main", "codex-main"])),
])
def test_direct_pattern_methods_reject_empty_or_duplicate_work(cfg, method, args):
    orch = orchestrator.Orchestrator(cfg, auto=True)

    with pytest.raises(orchestrator.RunAborted, match="비어|중복"):
        getattr(orch, method)(*args)


def test_fanout_join_worker_may_be_a_separate_configured_worker(cfg):
    spec = {
        "pattern": "fanout", "task": "t", "workers": ["codex-main"],
        "join_worker": "claude-main",
    }

    assert orchestrator._task_spec_error(spec, cfg) == ""
