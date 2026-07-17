from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from yok3x import orchestrator, reserve, usage
from yok3x.backends import BackendResult
from yok3x.config import DEFAULT_YOK3X, Config
from yok3x.orchestrator import CallSpec, Orchestrator, RunAborted


def _spec(position: int, backend: str = "mock") -> CallSpec:
    return CallSpec(
        worker=f"worker-{position}", task=str(position), task_kind="build",
        backend=backend, prompt=f"prompt-{position}", run_cwd=".")


def _orch(tmp_path, monkeypatch, *, max_workers=4, max_per_backend=2,
          enabled=True):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    cfg.yok3x["guard"]["parallel"].update(
        enabled=enabled, max_workers=max_workers,
        max_per_backend=max_per_backend)
    cfg.yok3x["guard"]["degrade"].update(
        failover_enabled=False, offline_enabled=False)
    for backend in ("fast", "stop", "same"):
        cfg.backends[backend] = {"type": "mock", "latency_sec": 0}
    for index in range(8):
        cfg.yok3x["workers"][f"worker-{index}"] = {
            "backend": "mock", "role": "test",
        }
    orch = Orchestrator(cfg, auto=True)

    def approve(specs):
        for spec in specs:
            spec.batch_approved = True
        return True

    monkeypatch.setattr(orch, "reserve_and_approve", approve)
    monkeypatch.setattr(
        usage, "check_backend",
        lambda cfg, backend: usage.GuardVerdict(
            backend, 0.1, "daily_calls", "ok", "여유"))
    monkeypatch.setattr(usage, "record", lambda *args, **kwargs: None)
    return orch


def test_parallel_returns_results_in_input_order(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=3)

    def backend(name, spec, prompt, **kwargs):
        position = int(prompt.rsplit("-", 1)[1])
        time.sleep({0: 0.09, 1: 0.04, 2: 0.01}[position])
        return BackendResult(name, True, text=str(position))

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    results = orch.call_workers_parallel([_spec(0), _spec(1), _spec(2)])

    assert [result.text for result in results if result is not None] == ["0", "1", "2"]


def test_parallel_really_enters_two_workers_concurrently(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=2, max_per_backend=2)
    lock = threading.Lock()
    both_entered = threading.Event()
    active = maximum = 0

    def backend(name, spec, prompt, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_entered.set()
        assert both_entered.wait(1.0)
        time.sleep(0.03)
        with lock:
            active -= 1
        return BackendResult(name, True, text=prompt)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    results = orch.call_workers_parallel([_spec(0), _spec(1)])

    assert all(result and result.ok for result in results)
    assert maximum == 2


def test_parallel_all_settled_preserves_other_results(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=3)

    def backend(name, spec, prompt, **kwargs):
        if prompt == "prompt-1":
            raise ValueError("boom")
        return BackendResult(name, True, text=prompt)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    results = orch.call_workers_parallel([_spec(0), _spec(1), _spec(2)])

    assert results[0] and results[0].text == "prompt-0"
    assert results[1] is None
    assert results[2] and results[2].text == "prompt-2"


def test_parallel_guard_abort_propagates_with_completed_results(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=2)
    fast_done = threading.Event()

    def check(cfg, backend):
        if backend == "stop":
            assert fast_done.wait(1.0)
            return usage.GuardVerdict("stop", 1.0, "daily_calls", "stop", "소진")
        return usage.GuardVerdict(backend, 0.1, "daily_calls", "ok", "여유")

    def backend(name, spec, prompt, **kwargs):
        fast_done.set()
        return BackendResult(name, True, text="fast-result")

    monkeypatch.setattr(usage, "check_backend", check)
    monkeypatch.setattr(usage, "failover_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "run_backend", backend)

    with pytest.raises(RunAborted) as caught:
        orch.call_workers_parallel([_spec(0, "fast"), _spec(1, "stop")])

    assert caught.value.results is not None
    assert caught.value.results[0] and caught.value.results[0].text == "fast-result"
    assert caught.value.results[1] is None


def test_parallel_limits_each_backend_with_semaphore(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=3, max_per_backend=2)
    lock = threading.Lock()
    active = maximum = 0

    def backend(name, spec, prompt, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return BackendResult(name, True, text=prompt)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    orch.call_workers_parallel([_spec(0, "same"), _spec(1, "same"), _spec(2, "same")])

    assert maximum == 2


def test_parallel_never_calls_worker_gate_and_assigns_sorted_indices(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=3)
    monkeypatch.setattr(
        orch, "_gate", lambda *args, **kwargs: pytest.fail("worker thread gate 금지"))

    def backend(name, spec, prompt, **kwargs):
        time.sleep((3 - int(prompt[-1])) * 0.02)
        return BackendResult(name, True, text=prompt)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    specs = [_spec(0), _spec(1), _spec(2)]
    orch.call_workers_parallel(specs)

    assert [spec.index for spec in specs] == [1, 2, 3]
    assert [step.index for step in orch.steps] == [1, 2, 3]


@pytest.mark.parametrize("mode", ["success", "failed-result", "exception"])
def test_parallel_always_releases_reservation(tmp_path, monkeypatch, mode):
    orch = _orch(tmp_path, monkeypatch, max_workers=1)
    released = []
    monkeypatch.setattr(
        reserve, "release", lambda cfg, run_id: released.append(run_id))

    def backend(name, spec, prompt, **kwargs):
        if mode == "exception":
            raise RuntimeError("backend exploded")
        return BackendResult(name, mode == "success", text=mode)

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    orch.call_workers_parallel([_spec(0)])

    assert released == [orch.run_id]


def test_parallel_disabled_uses_sequential_fallback(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, enabled=False)
    order = []
    control_thread = threading.get_ident()

    def execute(spec):
        assert threading.get_ident() == control_thread
        order.append(spec.task)
        return BackendResult(spec.backend, True, text=spec.task)

    monkeypatch.setattr(orch, "execute_call", execute)
    monkeypatch.setattr(
        orchestrator, "ThreadPoolExecutor",
        lambda *args, **kwargs: pytest.fail("기본 비활성에서 thread 생성 금지"))
    specs = [_spec(0), _spec(1), _spec(2)]

    results = orch.call_workers_parallel(specs)

    assert DEFAULT_YOK3X["guard"]["parallel"] == {
        "enabled": False, "max_workers": 4, "max_per_backend": 2,
    }
    assert order == ["0", "1", "2"]
    assert [result.text for result in results if result is not None] == order


@pytest.mark.skipif(os.name != "nt", reason="Windows CLI 프로세스 트리 취소 계약")
def test_parallel_abort_terminates_running_cli_process(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=2)
    pid_file = tmp_path / "child.pid"
    completed_file = tmp_path / "completed.txt"
    command = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(20); "
        f"pathlib.Path({str(completed_file)!r}).write_text('done')"
    )
    orch.cfg.backends["long-cli"] = {
        "type": "cli", "command": [sys.executable, "-c", command],
        "parser": "raw", "timeout_sec": 30,
    }

    def check(cfg, backend):
        if backend == "stop":
            deadline = time.monotonic() + 3
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pid_file.exists()
            return usage.GuardVerdict("stop", 1.0, "daily_calls", "stop", "소진")
        return usage.GuardVerdict(backend, 0.1, "daily_calls", "ok", "여유")

    monkeypatch.setattr(usage, "check_backend", check)
    monkeypatch.setattr(usage, "failover_backend", lambda *args, **kwargs: None)
    started = time.monotonic()

    with pytest.raises(RunAborted):
        orch.call_workers_parallel([
            _spec(0, "long-cli"), _spec(1, "stop"),
        ])

    assert time.monotonic() - started < 5
    assert not completed_file.exists()
