"""ACQUIRE preflight 오케스트레이터 배선 회귀 테스트."""
from __future__ import annotations

import copy
import json

import pytest

from yok3x import backends, knot, orchestrator, usage
from yok3x.backends import BackendResult, run_backend
from yok3x.config import DEFAULT_BACKENDS, Config, scaffold
from yok3x.orchestrator import Orchestrator, run_task_file


def _cfg(tmp_path):
    scaffold(tmp_path, use_mock=True)
    return Config.load(tmp_path)


def _verdict(level: str = "ok") -> usage.GuardVerdict:
    return usage.GuardVerdict("mock", 1.0 if level == "stop" else 0.1,
                              "5h", level, "test")


def _answer(text: str, *, evidence: bool = True) -> str:
    return json.dumps({
        "answer": text,
        "evidence": ([{"path": "yok3x/orchestrator.py", "symbol": "Orchestrator",
                       "observation": "호출 경로를 확인했다."}] if evidence else []),
        "confidence": "high",
        "unknowns": [],
        "alternative_hypotheses": [],
    }, ensure_ascii=False)


def test_acquire_calls_in_order_filters_and_saves_only_run_memory(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    orch = Orchestrator(cfg, auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    monkeypatch.setattr(knot, "save", lambda *a, **k: pytest.fail("QA를 knot에 저장하면 안 됨"))
    calls = []
    responses = iter([
        BackendResult("mock", True, json.dumps([
            {"category": "mechanism", "question": "첫 질문?"},
            {"category": "locating", "question": "둘째 질문?"},
        ], ensure_ascii=False)),
        BackendResult("mock", True, _answer("유효한 조사 결과")),
        BackendResult("mock", True, _answer("근거 없는 결과", evidence=False)),
    ])

    def fake_call(worker, task, task_kind="general", extra_context="", cwd=None,
                  read_only=False):
        calls.append((worker, task, task_kind, cwd, read_only))
        return next(responses)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    context, qa_items = orch.acquire_preflight(
        "이슈", "claude-main", ["codex-main"], qa_count=2, workdir=str(tmp_path))

    assert [call[0] for call in calls] == ["claude-main", "codex-main", "codex-main"]
    assert [call[4] for call in calls] == [False, True, True]
    assert all(call[2] == "general" and call[3] == str(tmp_path) for call in calls)
    assert "유효한 조사 결과" in context and "근거 없는 결과" not in context
    assert len(qa_items) == 1
    assert "유효한 조사 결과" not in calls[2][1]  # Answerer끼리 이전 답을 공유하지 않는다.

    saved = json.loads((orch.run_dir / "acquire.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == orch.run_id
    assert len(saved["questions"]) == 2 and saved["qa_items"] == qa_items


def test_acquire_guard_stop_skips_all_calls(tmp_path, monkeypatch):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict("stop"))
    monkeypatch.setattr(
        orch, "call_worker", lambda *a, **k: pytest.fail("guard stop에서 호출하면 안 됨"))

    assert orch.acquire_preflight("이슈", "claude-main", ["codex-main"]) == ("", [])
    assert not (orch.run_dir / "acquire.json").exists()


@pytest.mark.parametrize("raw", ["질문을 만들지 못함", "[]", "{}"])
def test_acquire_question_parse_failure_is_safe(tmp_path, monkeypatch, raw):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    calls = []

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return BackendResult("mock", True, raw)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    assert orch.acquire_preflight("이슈", "claude-main", ["codex-main"]) == ("", [])
    assert len(calls) == 1


@pytest.mark.parametrize("backend_name, expected", [
    ("codex", ["--sandbox", "read-only"]),
    ("gemini", ["--approval-mode", "plan"]),
    ("claude", ["--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit"]),
])
def test_cli_read_only_profile_is_reflected_in_argv(monkeypatch, backend_name, expected):
    seen = {}

    class _Proc:
        stdout, stderr, returncode = "ok", "", 0

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends.shutil, "which", lambda command: None)
    spec = copy.deepcopy(DEFAULT_BACKENDS[backend_name])
    spec["parser"] = "raw"
    assert run_backend(backend_name, spec, "조사", read_only=True).ok
    cmd = seen["cmd"]
    start = cmd.index(expected[0])
    assert cmd[start:start + len(expected)] == expected
    if backend_name == "claude":
        assert "Read" not in cmd[start + 1] and "Grep" not in cmd[start + 1]
        assert "Glob" not in cmd[start + 1] and "Bash" not in cmd[start + 1]


def test_call_worker_forwards_read_only_to_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.yok3x["workers"]["codex-main"]["backend"] = "codex"
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    captured = {}

    def fake_backend(name, spec, prompt, **kwargs):
        captured.update(name=name, kwargs=kwargs)
        return BackendResult(name, True, "{}")

    monkeypatch.setattr(orchestrator, "run_backend", fake_backend)
    Orchestrator(cfg, auto=True).call_worker(
        "codex-main", "읽기 조사", "general", cwd=str(tmp_path), read_only=True)
    assert captured["name"] == "codex" and captured["kwargs"]["read_only"] is True


def test_run_task_file_without_acquire_keeps_existing_flow(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps({
        "pattern": "pipeline", "task": "기존 작업",
        "stages": [{"worker": "claude-main", "kind": "build"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        Orchestrator, "acquire_preflight",
        lambda *a, **k: pytest.fail("acquire 옵션이 없으면 preflight를 호출하면 안 됨"))

    assert run_task_file(cfg, task_file, auto=True) == "done"


def test_task_acquire_context_is_prefixed_once_to_first_pipeline_stage(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps({
        "pattern": "pipeline", "task": "수리 작업",
        "stages": [
            {"worker": "claude-main", "kind": "build"},
            {"worker": "codex-main", "kind": "review"},
        ],
        "acquire": {"questioner": "claude-main", "answerers": ["codex-main"],
                    "qa_count": 2},
    }, ensure_ascii=False), encoding="utf-8")
    preflight = {}
    calls = []

    def fake_acquire(self, issue, questioner, answerers, qa_count=2, workdir=None):
        preflight.update(issue=issue, questioner=questioner, answerers=answerers,
                         qa_count=qa_count, workdir=workdir)
        return "[ACQUIRE QA]", []

    def fake_call(self, worker, task, task_kind="general", extra_context="", cwd=None,
                  read_only=False):
        calls.append((worker, task_kind, extra_context))
        return BackendResult("mock", True, f"output-{len(calls)}")

    monkeypatch.setattr(Orchestrator, "acquire_preflight", fake_acquire)
    monkeypatch.setattr(Orchestrator, "call_worker", fake_call)
    assert run_task_file(cfg, task_file, auto=True) == "done"

    assert preflight == {"issue": "수리 작업", "questioner": "claude-main",
                         "answerers": ["codex-main"], "qa_count": 2, "workdir": None}
    assert calls[0][2].startswith("[ACQUIRE QA]")
    assert "[ACQUIRE QA]" not in calls[1][2]
