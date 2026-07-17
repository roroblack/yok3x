from __future__ import annotations

import json
import os
import time

import pytest

from yok3x import orchestrator, reserve, usage
from yok3x.backends import BackendResult
from yok3x.config import Config
from yok3x.orchestrator import CallSpec, Orchestrator, RunAborted


def _cfg(tmp_path) -> Config:
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["use_real_limits"] = False
    cfg.yok3x["guard"]["reservation"]["lock_wait_sec"] = 0.05
    cfg.yok3x["budgets"] = {
        "codex": {"daily_calls": 2, "daily_tokens": 100, "daily_usd": 1.0},
    }
    return cfg


def test_file_lock_acquires_releases_and_blocks_duplicate(tmp_path):
    path = tmp_path / "reservations.lock"
    with reserve.file_lock(path, run_id="first"):
        owner = json.loads(path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid() and owner["run_id"] == "first"
        with pytest.raises(FileExistsError):
            with reserve.file_lock(path, run_id="second"):
                pytest.fail("중복 lock을 획득하면 안 됨")
    assert not path.exists()


def test_file_lock_recovers_stale_other_pid(tmp_path, capsys):
    path = tmp_path / "reservations.lock"
    path.write_text(json.dumps({
        "pid": 999999, "run_id": "dead-run", "ts": time.time() - 60,
    }), encoding="utf-8")

    with reserve.file_lock(path, ttl=1, run_id="new-run"):
        owner = json.loads(path.read_text(encoding="utf-8"))
        assert owner["run_id"] == "new-run"

    assert "stale lock 회수" in capsys.readouterr().out
    assert not path.exists()


def test_reserve_records_rejects_combined_hard_limit_and_releases(tmp_path):
    cfg = _cfg(tmp_path)

    assert reserve.reserve(cfg, "run-1", calls=1, est_tokens=40, est_usd=0.4)
    ledger_path = cfg.paths.yok3x_dir / "reservations.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["run-1"]["calls"] == 1
    assert ledger["run-1"]["est_tokens"] == 40
    assert ledger["run-1"]["est_usd"] == 0.4

    # 각각만 보면 2-call hard 상한 이하지만 pending과 합치면 3이라 두 번째를 거절한다.
    assert not reserve.reserve(cfg, "run-2", calls=2, est_tokens=40, est_usd=0.4)
    assert "run-2" not in json.loads(ledger_path.read_text(encoding="utf-8"))

    reserve.release(cfg, "run-1")
    assert reserve.reserve(cfg, "run-2", calls=2, est_tokens=80, est_usd=0.8)


def test_reserve_includes_today_usage_before_pending(tmp_path):
    cfg = _cfg(tmp_path)
    usage.record(cfg, "codex-main", "build", BackendResult(
        backend="codex", ok=True, total_tokens=60, cost_usd=0.6))

    assert reserve.reserve(cfg, "run-1", calls=1, est_tokens=40, est_usd=0.4)
    assert not reserve.reserve(cfg, "run-2", calls=1, est_tokens=1, est_usd=0.01)


def test_cleanup_stale_removes_only_expired_reservations(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.paths.yok3x_dir.mkdir(parents=True)
    now = time.time()
    ledger_path = cfg.paths.yok3x_dir / "reservations.json"
    ledger_path.write_text(json.dumps({
        "old": {"calls": 1, "est_tokens": 10, "est_usd": 0.1,
                "ts": now - 100, "pid": 111},
        "new": {"calls": 1, "est_tokens": 10, "est_usd": 0.1,
                "ts": now, "pid": 222},
    }), encoding="utf-8")

    assert reserve.cleanup_stale(cfg, ttl=10) == 1
    assert set(json.loads(ledger_path.read_text(encoding="utf-8"))) == {"new"}


def _spec(worker="codex-main", backend="codex", task_kind="build") -> CallSpec:
    return CallSpec(worker=worker, task="작업", task_kind=task_kind,
                    backend=backend, prompt="긴 프롬프트" * 10, run_cwd=".")


def test_approve_batch_auto_logs_workers_backends_kinds_and_estimates(tmp_path, capsys):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    specs = [_spec(), _spec("codex-critic", "codex", "critic")]

    assert orch.approve_batch(specs)
    shown = capsys.readouterr().out
    assert "worker=codex-main" in shown and "worker=codex-critic" in shown
    assert "backend=codex" in shown
    assert "task_kind=build" in shown and "task_kind=critic" in shown
    assert "calls=2(정확)" in shown and "tokens≈" in shown and "USD≈" in shown
    assert all(spec.batch_approved for spec in specs)


def test_approve_batch_rejects_and_q_aborts(tmp_path):
    spec = _spec()
    assert not Orchestrator(_cfg(tmp_path), auto=False, ask=lambda _: "n").approve_batch([spec])
    assert spec.batch_approved is False
    with pytest.raises(RunAborted):
        Orchestrator(_cfg(tmp_path), auto=False, ask=lambda _: "q").approve_batch([_spec()])


def test_reserve_and_approve_does_not_ask_after_reservation_failure(tmp_path, monkeypatch):
    orch = Orchestrator(_cfg(tmp_path), auto=False, ask=lambda _: pytest.fail("승인을 물으면 안 됨"))
    monkeypatch.setattr(reserve, "cleanup_stale", lambda *a, **k: 0)
    monkeypatch.setattr(reserve, "reserve", lambda *a, **k: False)

    assert not orch.reserve_and_approve([_spec()])


def test_reserve_and_approve_rejection_releases_pending(tmp_path):
    cfg = _cfg(tmp_path)
    orch = Orchestrator(cfg, auto=False, ask=lambda _: "n")

    assert not orch.reserve_and_approve([_spec()])
    ledger = json.loads(
        (cfg.paths.yok3x_dir / "reservations.json").read_text(encoding="utf-8"))
    assert orch.run_id not in ledger


def test_batch_approved_execute_call_never_asks_worker_input(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    orch = Orchestrator(cfg, auto=True, ask=lambda _: pytest.fail("worker input 금지"))
    spec = _spec()
    assert orch.approve_batch([spec])
    verdict = usage.GuardVerdict("codex", 0.1, "daily_calls", "ok", "여유")
    monkeypatch.setattr(usage, "check_backend", lambda *a, **k: verdict)
    monkeypatch.setattr(usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "run_backend", lambda *a, **k: BackendResult(
        backend="codex", ok=True, text="완료"))

    assert orch.execute_call(spec).ok


def test_estimate_call_is_configurable_and_explicitly_approximate(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.yok3x["guard"]["reservation"].update(
        chars_per_token=1.0, output_token_ratio=1.0, usd_per_1k_tokens=0.5)
    orch = Orchestrator(cfg, auto=True)
    spec = CallSpec(worker="codex-main", task="t", backend="codex", prompt="1234567890")

    assert orch.estimate_call(spec) == (20, 0.01)
