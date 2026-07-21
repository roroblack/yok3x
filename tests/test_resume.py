from __future__ import annotations

import json
from pathlib import Path

import pytest

from yok3x import orchestrator, reserve, usage
from yok3x.backends import BackendResult
from yok3x.config import Config, scaffold
from yok3x.orchestrator import CallSpec, RunAborted, run_task_file


@pytest.fixture
def resume_env(tmp_path, monkeypatch):
    scaffold(tmp_path, use_mock=True)
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    cfg.yok3x["guard"]["use_real_limits"] = False
    verdict = usage.GuardVerdict("mock", 0.1, "daily_calls", "ok", "여유")
    monkeypatch.setattr(usage, "check_backend", lambda *a, **k: verdict)
    monkeypatch.setattr(usage, "failover_backend", lambda *a, **k: None)
    task_file = tmp_path / "pipeline.json"
    task_file.write_text(json.dumps({
        "pattern": "pipeline", "task": "재개 테스트",
        "stages": [
            {"worker": "claude-main", "kind": "build", "task": "첫째"},
            {"worker": "codex-main", "kind": "build", "task": "둘째"},
            {"worker": "codex-critic", "kind": "review", "task": "셋째"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return cfg, task_file


def _run_ids(cfg: Config) -> list[str]:
    return sorted(path.name for path in cfg.paths.runs.glob("run_*") if path.is_dir())


def _latest_resumed_status(cfg: Config, source_run_id: str) -> dict:
    statuses = []
    for path in cfg.paths.runs.glob("run_*/status.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("resume_from") == source_run_id:
            statuses.append((path.stat().st_mtime_ns, data))
    return max(statuses, key=lambda item: item[0])[1]


def _complete_run(resume_env, monkeypatch, *, verify=False) -> tuple[Config, Path, str]:
    cfg, task_file = resume_env
    outputs = iter(("one", "two", "three"))
    monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: BackendResult(
        backend="mock", ok=True, text=next(outputs), total_tokens=10, cost_usd=0.1,
        duration_ms=5))
    if verify:
        cfg.yok3x["verify_cmd"] = "verify-sentinel"
        monkeypatch.setattr(orchestrator.Orchestrator, "_run_verify", lambda self: (True, "ok"))
    assert run_task_file(cfg, task_file, auto=True) == "done"
    return cfg, task_file, _run_ids(cfg)[-1]


def test_call_key_is_deterministic_and_content_based():
    base = CallSpec("w", "task", "build", backend="mock", model="m",
                    prompt="prompt", read_only=False)
    same = CallSpec("w", "다른 표시용 task", "build", backend="mock", model="m",
                    prompt="prompt", read_only=False)
    changed = CallSpec("w", "task", "build", backend="mock", model="m",
                       prompt="prompt!", read_only=False)

    assert base.call_key == same.call_key
    assert len(base.call_key) == 16
    assert base.call_key != changed.call_key


def test_abort_status_preserves_cause_and_resumable(resume_env, monkeypatch):
    cfg, task_file = resume_env
    monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: (_ for _ in ()).throw(
        RunAborted("사용자 중단", cause="user_abort")))

    assert str(run_task_file(cfg, task_file, auto=True)).startswith("aborted")
    status = json.loads((cfg.paths.runs / _run_ids(cfg)[-1] / "status.json").read_text(
        encoding="utf-8"))
    assert status["state"] == "aborted"
    assert status["cause"] == "user_abort"
    assert status["resumable"] is True


def test_guard_stop_outer_handler_keeps_machine_readable_cause(resume_env, monkeypatch):
    cfg, task_file = resume_env
    stop = usage.GuardVerdict("mock", 1.0, "daily_calls", "stop", "소진")
    monkeypatch.setattr(usage, "check_backend", lambda *a, **k: stop)
    monkeypatch.setattr(usage, "failover_backend", lambda *a, **k: None)

    assert str(run_task_file(cfg, task_file, auto=True)).startswith("aborted")
    status = json.loads((cfg.paths.runs / _run_ids(cfg)[-1] / "status.json").read_text(
        encoding="utf-8"))
    assert status["state"] == "aborted"
    assert status["cause"] == "guard_stop"
    assert status["resumable"] is True


def test_resume_replays_success_prefix_without_backend_or_usage(resume_env, monkeypatch):
    cfg, task_file = resume_env
    first_calls = 0

    def interrupted(*args, **kwargs):
        nonlocal first_calls
        first_calls += 1
        if first_calls == 2:
            raise RunAborted("중단", cause="user_abort")
        return BackendResult("mock", True, text="one", total_tokens=11, cost_usd=0.2)

    monkeypatch.setattr(orchestrator, "run_backend", interrupted)
    assert str(run_task_file(cfg, task_file, auto=True)).startswith("aborted")
    source_run_id = _run_ids(cfg)[-1]

    backend_prompts = []
    usage_calls = []
    outputs = iter(("two", "three"))

    def backend(name, backend_spec, prompt, **kwargs):
        backend_prompts.append(prompt)
        return BackendResult(name, True, text=next(outputs), total_tokens=7, cost_usd=0.1)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    monkeypatch.setattr(usage, "record", lambda *a, **k: usage_calls.append(a))

    assert run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id) == "done"
    assert len(backend_prompts) == 2
    assert len(usage_calls) == 2
    status = _latest_resumed_status(cfg, source_run_id)
    assert [step["replayed"] for step in status["steps"]] == [True, False, False]
    replay_file = cfg.paths.runs / status["run_id"] / "step_01_claude-main.json"
    assert json.loads(replay_file.read_text(encoding="utf-8"))["replayed"] is True


def _pr_task_file(cfg: Config) -> Path:
    """G-2: producer-reviewer 재개 테스트용 task 파일."""
    path = cfg.paths.root / "pr.json"
    path.write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "재개 테스트 PR",
        "producer": "claude-main", "reviewer": "codex-critic",
        "max_rounds": 3, "pass_score": 8.0,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_producer_reviewer_resume_replays_completed_rounds(resume_env, monkeypatch):
    """G-2: 라운드1(producer+reviewer) 완료 후 라운드2 producer에서 중단 →
    재개 시 라운드1 두 호출은 재생(백엔드/과금 0), 라운드2부터 실제 실행."""
    cfg, _ = resume_env
    task_file = _pr_task_file(cfg)

    # 원런: call1 producer→draft1, call2 reviewer→SCORE 5(계속), call3 라운드2 producer→중단
    calls = 0

    def interrupted(name, backend_spec, prompt, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return BackendResult(name, True, text="draft1", total_tokens=10, cost_usd=0.1)
        if calls == 2:
            return BackendResult(name, True, text="SCORE: 5\n결함 A", total_tokens=10, cost_usd=0.1)
        raise RunAborted("중단", cause="user_abort")

    monkeypatch.setattr(orchestrator, "run_backend", interrupted)
    assert str(run_task_file(cfg, task_file, auto=True)).startswith("aborted")
    source_run_id = _run_ids(cfg)[-1]

    # 재개: 라운드2 producer→draft2, 라운드2 reviewer→SCORE 9(통과) 만 실제 호출돼야
    prompts = []
    usage_calls = []
    outputs = iter(("draft2", "SCORE: 9\n통과"))

    def backend(name, backend_spec, prompt, **kwargs):
        prompts.append(prompt)
        return BackendResult(name, True, text=next(outputs), total_tokens=7, cost_usd=0.1)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    monkeypatch.setattr(usage, "record", lambda *a, **k: usage_calls.append(a))

    assert run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id) == "done"
    # 라운드1 두 호출은 재생(실제 백엔드 0), 라운드2 두 호출만 실제
    assert len(prompts) == 2
    assert len(usage_calls) == 2
    status = _latest_resumed_status(cfg, source_run_id)
    assert [step["replayed"] for step in status["steps"]] == [True, True, False, False]
    # 게이트 통과로 완료
    assert status["gate"]["passed"] is True


def test_producer_reviewer_resume_recomputes_escalate_state(resume_env, monkeypatch):
    """G-2 핵심 위험: escalate로 producer가 교체된 뒤 중단 → 재개 시 교체 상태가
    재생된 라운드 결과로 결정적으로 재계산돼야(라운드3 producer=codex-main) call_key가 맞아 재생/실행."""
    cfg, _ = resume_env
    path = cfg.paths.root / "pr_esc.json"
    path.write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "escalate 재개",
        "producer": "claude-main", "reviewer": "codex-critic",
        "max_rounds": 4, "pass_score": 8.0,
        "escalate": {"after_round": 2, "if_score_below": 6, "to_producer": "codex-main"},
    }, ensure_ascii=False), encoding="utf-8")

    # 원런: r1(prod,rev SCORE5) r2(prod,rev SCORE5→escalate) r3 prod(codex-main)에서 중단
    seq = iter([
        ("draft1", "claude-main"), ("SCORE: 5\nA", "codex-critic"),
        ("draft2", "claude-main"), ("SCORE: 5\nA", "codex-critic"),
    ])
    calls = 0

    def interrupted(name, backend_spec, prompt, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 4:
            text, _ = next(seq)
            return BackendResult(name, True, text=text, total_tokens=10, cost_usd=0.1)
        raise RunAborted("중단", cause="user_abort")

    monkeypatch.setattr(orchestrator, "run_backend", interrupted)
    assert str(run_task_file(cfg, path, auto=True)).startswith("aborted")
    source_run_id = _run_ids(cfg)[-1]

    # 재개: r1·r2 4호출 재생, r3 producer(codex-main)→draft3, r3 reviewer→SCORE 9(통과)만 실제
    seen_workers = []
    outputs = iter(("draft3", "SCORE: 9\nok"))

    def backend(name, backend_spec, prompt, **kwargs):
        seen_workers.append(name)
        return BackendResult(name, True, text=next(outputs), total_tokens=7, cost_usd=0.1)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    assert run_task_file(cfg, path, auto=True, resume_run_id=source_run_id) == "done"
    # r1·r2 재생 후 r3부터 실제 — r3 producer가 escalate된 codex-main이어야(교체 상태 재계산 증명)
    status = _latest_resumed_status(cfg, source_run_id)
    replayed = [s["replayed"] for s in status["steps"]]
    assert replayed[:4] == [True, True, True, True] and replayed[4] is False
    assert status["steps"][4]["worker"] == "codex-main"  # escalate 재계산됨
    assert status["gate"]["passed"] is True


def test_manifest_rejects_spec_and_worker_model_drift(resume_env, monkeypatch):
    cfg, task_file, source_run_id = _complete_run(resume_env, monkeypatch)
    original = task_file.read_text(encoding="utf-8")
    task_file.write_text(original + "\n", encoding="utf-8")
    result = run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id)
    assert "spec_sha256" in result["error"]

    task_file.write_text(original, encoding="utf-8")
    cfg.yok3x["workers"]["claude-main"]["model"] = "changed-model"
    result = run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id)
    assert "workers.claude-main.model" in result["error"]


@pytest.mark.parametrize("bad_kind", ["failed", "blocked"])
def test_failed_or_blocked_step_is_not_cached(resume_env, monkeypatch, bad_kind):
    cfg, task_file = resume_env
    if bad_kind == "failed":
        results = iter((
            BackendResult("mock", True, text="one"),
            BackendResult("mock", False, error="failed"),
            BackendResult("mock", True, text="old-three"),
        ))
        monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: next(results))
        assert run_task_file(cfg, task_file, auto=True) == "done"
    else:
        calls = 0

        def blocked(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RunAborted("guard", cause="guard_stop")
            return BackendResult("mock", True, text="one")

        monkeypatch.setattr(orchestrator, "run_backend", blocked)
        assert str(run_task_file(cfg, task_file, auto=True)).startswith("aborted")
    source_run_id = _run_ids(cfg)[-1]

    actual = []
    outputs = iter(("new-two", "new-three"))

    def backend(*args, **kwargs):
        actual.append(1)
        return BackendResult("mock", True, text=next(outputs))

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    assert run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id) == "done"
    assert len(actual) == 2


def test_corrupt_step_stops_cache_at_that_point(resume_env, monkeypatch):
    cfg, task_file, source_run_id = _complete_run(resume_env, monkeypatch)
    (cfg.paths.runs / source_run_id / "step_02_codex-main.json").write_text(
        "{broken", encoding="utf-8")
    actual = []
    outputs = iter(("two-new", "three-new"))
    monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: (
        actual.append(1) or BackendResult("mock", True, text=next(outputs))))

    assert run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id) == "done"
    assert len(actual) == 2


def test_verify_is_always_rerun_when_calls_are_replayed(resume_env, monkeypatch):
    cfg, task_file, source_run_id = _complete_run(resume_env, monkeypatch, verify=True)
    verify_calls = []
    monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: pytest.fail(
        "성공 prefix backend를 다시 호출하면 안 됨"))
    monkeypatch.setattr(orchestrator.Orchestrator, "_run_verify", lambda self: (
        verify_calls.append(self.run_id) or (True, "rerun")))

    assert run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id) == "done"
    assert len(verify_calls) == 1


def test_resume_rejects_non_pipeline_parallel_and_extended_specs(resume_env, monkeypatch):
    cfg, task_file, source_run_id = _complete_run(resume_env, monkeypatch)
    spec = json.loads(task_file.read_text(encoding="utf-8"))

    spec["pattern"] = "fanout"
    spec["workers"] = ["claude-main"]
    task_file.write_text(json.dumps(spec), encoding="utf-8")
    assert "pattern=pipeline" in run_task_file(
        cfg, task_file, auto=True, resume_run_id=source_run_id)["error"]

    spec["pattern"] = "pipeline"
    task_file.write_text(json.dumps(spec), encoding="utf-8")
    cfg.yok3x["guard"]["parallel"]["enabled"] = True
    assert "parallel.enabled=false" in run_task_file(
        cfg, task_file, auto=True, resume_run_id=source_run_id)["error"]

    cfg.yok3x["guard"]["parallel"]["enabled"] = False
    for key in ("acquire", "materialize"):
        spec[key] = {}
        task_file.write_text(json.dumps(spec), encoding="utf-8")
        assert key in run_task_file(
            cfg, task_file, auto=True, resume_run_id=source_run_id)["error"]
        del spec[key]


def test_lineage_lock_blocks_concurrent_resume(resume_env, monkeypatch):
    cfg, task_file, source_run_id = _complete_run(resume_env, monkeypatch)
    lock_path = cfg.paths.runs / source_run_id / "resume.lock"

    with reserve.file_lock(lock_path, ttl=86400, run_id="first-resumer"):
        result = run_task_file(cfg, task_file, auto=True, resume_run_id=source_run_id)

    assert "lineage 잠금 사용 중" in result["error"]
