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

    # 기본값 고정 가드: 병렬·격리 모두 opt-in이어야 한다(R-7 worktree_isolation 추가분 포함).
    assert DEFAULT_YOK3X["guard"]["parallel"] == {
        "enabled": False, "max_workers": 4, "max_per_backend": 2,
        "worktree_isolation": False,
    }
    assert order == ["0", "1", "2"]
    assert [result.text for result in results if result is not None] == order


def test_fanout_runs_concurrently_and_keeps_worker_order(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch, max_workers=3, max_per_backend=3)
    workers = ["worker-0", "worker-1", "worker-2"]
    delays = {"worker-0": 0.18, "worker-1": 0.05, "worker-2": 0.10}
    for worker in workers:
        orch.cfg.yok3x["workers"][worker]["role"] = worker
    lock = threading.Lock()
    active = maximum = 0
    final = []

    def backend(name, spec, prompt, **kwargs):
        nonlocal active, maximum
        worker = next(worker for worker in workers if f"[역할] {worker}" in prompt)
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(delays[worker])
        with lock:
            active -= 1
        return BackendResult(name, True, text=f"result-{worker}")

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    monkeypatch.setattr(orch, "_finish", lambda task, output: final.append(output))
    started = time.monotonic()

    orch.run_fanout("병렬 fanout", workers)

    elapsed = time.monotonic() - started
    # 병렬성의 확실한 증거는 '동시 진입 최대치'다(세 워커가 실제로 겹쳐 실행). 벽시계 임계는
    # 스레드풀·예약·배치승인 고정 오버헤드 때문에 sum*0.75처럼 빡빡하면 머신에 따라 flaky하다
    # → 순차보다 빠르다(< 합)만 sanity로 확인한다(BUG: 이 임계가 CI에서 간헐 실패).
    assert maximum == 3
    assert elapsed < sum(delays.values())
    assert final == [
        "### worker-0\nresult-worker-0\n\n"
        "### worker-1\nresult-worker-1\n\n"
        "### worker-2\nresult-worker-2"
    ]


def test_fanout_prepares_ordered_specs_with_context_only_on_first_worker(
        tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)
    workers = ["worker-0", "worker-1", "worker-2"]
    captured = []
    final = []

    def parallel(specs):
        captured.extend(specs)
        return [BackendResult("mock", True, text=spec.worker) for spec in specs]

    monkeypatch.setattr(orch, "call_workers_parallel", parallel)
    monkeypatch.setattr(orch, "_finish", lambda task, output: final.append(output))

    orch.run_fanout("같은 작업", workers, initial_context="첫 워커 QA")

    assert [spec.worker for spec in captured] == workers
    assert [spec.task for spec in captured] == ["같은 작업"] * 3
    assert [spec.task_kind for spec in captured] == ["fanout"] * 3
    assert [spec.extra_context for spec in captured] == ["첫 워커 QA", "", ""]
    assert [part.splitlines()[0] for part in final[0].split("\n\n")] == [
        "### worker-0", "### worker-1", "### worker-2",
    ]


@pytest.mark.parametrize("failure", ["exception", "not-ok"])
def test_fanout_failure_slot_keeps_other_results(tmp_path, monkeypatch, failure):
    orch = _orch(tmp_path, monkeypatch, max_workers=3, max_per_backend=3)
    workers = ["worker-0", "worker-1", "worker-2"]
    for worker in workers:
        orch.cfg.yok3x["workers"][worker]["role"] = worker
    final = []

    def backend(name, spec, prompt, **kwargs):
        worker = next(worker for worker in workers if f"[역할] {worker}" in prompt)
        if worker == "worker-1":
            if failure == "exception":
                raise RuntimeError("fanout boom")
            return BackendResult(name, False, error="fanout failed")
        return BackendResult(name, True, text=f"result-{worker}")

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    monkeypatch.setattr(orch, "_finish", lambda task, output: final.append(output))

    orch.run_fanout("부분 성공", workers)

    assert final == [
        "### worker-0\nresult-worker-0\n\n"
        "### worker-2\nresult-worker-2"
    ]


def test_fanout_join_worker_is_one_sequential_call(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)
    workers = ["worker-0", "worker-1"]
    parallel_calls = []
    join_calls = []
    final = []

    def parallel(specs):
        parallel_calls.append(specs)
        return [
            BackendResult("mock", True, text="first"),
            BackendResult("mock", True, text="second"),
        ]

    def join(worker, task, task_kind="general", extra_context="", cwd=None,
             read_only=False):
        join_calls.append((worker, task, task_kind, extra_context))
        return BackendResult("mock", True, text="joined-result")

    monkeypatch.setattr(orch, "call_workers_parallel", parallel)
    monkeypatch.setattr(orch, "call_worker", join)
    monkeypatch.setattr(orch, "_finish", lambda task, output: final.append(output))

    orch.run_fanout("취합 작업", workers, join_worker="worker-2")

    assert len(parallel_calls) == 1
    assert len(join_calls) == 1
    assert join_calls[0][:3] == (
        "worker-2", "아래 여러 워커의 결과를 하나의 최종안으로 통합하라.", "fanin")
    assert join_calls[0][3] == "### worker-0\nfirst\n\n### worker-1\nsecond"
    assert final == ["joined-result"]


def test_fanout_parallel_on_off_returns_same_result(tmp_path, monkeypatch):
    outputs = {}

    def backend(name, spec, prompt, **kwargs):
        worker = next(
            worker for worker in ("worker-0", "worker-1")
            if f"[역할] {worker}" in prompt)
        return BackendResult(name, True, text=f"result-{worker}")

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    for enabled in (False, True):
        root = tmp_path / ("on" if enabled else "off")
        orch = _orch(root, monkeypatch, enabled=enabled, max_workers=2,
                     max_per_backend=2)
        workers = ["worker-0", "worker-1"]
        for worker in workers:
            orch.cfg.yok3x["workers"][worker]["role"] = worker
        monkeypatch.setattr(
            orch, "_finish",
            lambda task, output, enabled=enabled: outputs.__setitem__(enabled, output))

        orch.run_fanout("동일 결과", workers)

    assert outputs[False] == outputs[True] == (
        "### worker-0\nresult-worker-0\n\n"
        "### worker-1\nresult-worker-1")


def test_fanout_parallel_run_aborted_propagates(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)

    def abort(specs):
        raise RunAborted("guard stop")

    monkeypatch.setattr(orch, "call_workers_parallel", abort)
    monkeypatch.setattr(
        orch, "_finish", lambda *args: pytest.fail("중단된 fanout을 완료하면 안 됨"))

    with pytest.raises(RunAborted, match="guard stop"):
        orch.run_fanout("중단 작업", ["worker-0", "worker-1"])


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
