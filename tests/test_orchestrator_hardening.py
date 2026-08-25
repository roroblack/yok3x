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
    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    assert len(succeeded) == 1 and len(failed) == 1
    # 실패한 쪽은 반드시 명시적으로 실패해야 한다 — TOCTOU 거부(rejected)든, 매니페스트
    # 쓰기 등 다른 단계의 보고된 에러(reason)든, 조용히 덮어쓴 것처럼 보이면 안 된다.
    # (v4.x stage-then-publish가 매니페스트 쓰기라는 새 공유 자원 접근을 추가했으므로,
    # 실패 사유가 항상 TOCTOU 거부일 필요는 없다 — "절대 조용히 안 넘어간다"만 보장하면 된다.)
    assert failed[0]["rejected"] or failed[0].get("reason")
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


def test_materialize_staging_failure_leaves_root_untouched(cfg, monkeypatch):
    """v4.x stage-then-publish: 스테이징 단계 실패는 root를 전혀 안 건드려야 한다."""
    root = cfg.paths.yok3x_dir.parent / "staging-fail-output"
    orch = orchestrator.Orchestrator(cfg, auto=True)
    orch.materialize = {"enabled": True, "root": str(root), "overwrite": True}
    real_write = orchestrator._atomic_write_bytes

    def failing_write(path, data, **kwargs):
        if path.name == "b.txt":
            raise OSError("simulated disk-full")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(orchestrator, "_atomic_write_bytes", failing_write)

    result = orch._materialize_outputs(
        "```file:a.txt\nA\n```\n```file:b.txt\nB\n```")

    assert result["ok"] is False
    assert not root.exists()


def test_materialize_commit_failure_preserves_manifest_and_partial_state(cfg, monkeypatch):
    """v4.x stage-then-publish: 커밋 도중 실패는 매니페스트를 남기고(고아 감지용),
    스테이징 디렉터리는 정리한다 — 이미 이동된 파일이 남는 건 계획서가 명시한 잔여 위험."""
    root = cfg.paths.yok3x_dir.parent / "commit-fail-output"
    orch = orchestrator.Orchestrator(cfg, auto=True)
    orch.materialize = {"enabled": True, "root": str(root), "overwrite": True}
    real_replace = orchestrator.os.replace

    def failing_replace(src, dst):
        # 스테이징 단계도 내부적으로 os.replace를 쓰므로, staging 디렉터리 안의
        # 쓰기는 건드리지 않고 root로의 최종 커밋 이동만 실패시킨다.
        if str(dst).endswith("b.txt") and ".materialize-staging-" not in str(dst):
            raise OSError("simulated crash mid-commit")
        return real_replace(src, dst)

    monkeypatch.setattr(orchestrator.os, "replace", failing_replace)

    result = orch._materialize_outputs(
        "```file:a.txt\nA\n```\n```file:b.txt\nB\n```")

    assert result["ok"] is False
    manifests = list(root.glob(".materialize-manifest-*.json"))
    assert len(manifests) == 1
    assert (root / "a.txt").exists()
    assert not (root / "b.txt").exists()
    assert not list(root.parent.glob(".materialize-staging-*"))


def test_materialize_warns_on_orphan_manifest_from_previous_run(cfg, monkeypatch):
    """v4.x: 이전 실행이 중간에 죽어 못 지운 매니페스트를 발견하면 경고만 남기고 자동 삭제 안 함."""
    root = cfg.paths.yok3x_dir.parent / "orphan-output"
    root.mkdir()
    orphan = root / ".materialize-manifest-stale-run.json"
    orphan.write_text("{}", encoding="utf-8")
    orch = orchestrator.Orchestrator(cfg, auto=True)
    orch.materialize = {"enabled": True, "root": str(root), "overwrite": True}
    logged: list[str] = []
    monkeypatch.setattr(orch, "_log", logged.append)

    result = orch._materialize_outputs("```file:c.txt\nC\n```")

    assert result["ok"] is True
    assert any("고아" in line and "매니페스트" in line for line in logged)
    assert orphan.exists()   # 자동 삭제하지 않는다(계획서 §3.1의 관측-only 결정)
