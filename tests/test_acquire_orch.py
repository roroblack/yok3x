"""ACQUIRE preflight 오케스트레이터 배선 회귀 테스트."""
from __future__ import annotations

import copy
import json
import threading
import time

import pytest

from yok3x import backends, knot, orchestrator, usage
from yok3x.backends import BackendResult, run_backend
from yok3x.config import DEFAULT_BACKENDS, Config, scaffold
from yok3x.orchestrator import Orchestrator, RunAborted, run_task_file, verify_evidence


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


def _write_evidence_repo(tmp_path):
    source = tmp_path / "yok3x" / "orchestrator.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("class Orchestrator:\n    pass\n", encoding="utf-8")


def _parallel_acquire_orch(tmp_path, monkeypatch, *, enabled=True):
    cfg = _cfg(tmp_path)
    cfg.yok3x["guard"]["parallel"].update(
        enabled=enabled, max_workers=3, max_per_backend=3)
    orch = Orchestrator(cfg, auto=True)
    captured_specs = []

    def approve(specs):
        captured_specs.extend(specs)
        for spec in specs:
            spec.batch_approved = True
        return True

    monkeypatch.setattr(orch, "reserve_and_approve", approve)
    monkeypatch.setattr(orchestrator.reserve, "release", lambda *args, **kwargs: None)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    monkeypatch.setattr(usage, "record", lambda *args, **kwargs: None)
    return orch, captured_specs


def test_verify_evidence_checks_path_and_symbol_with_host_filesystem(tmp_path):
    _write_evidence_repo(tmp_path)
    qa_items = [
        {"question": "존재", "answer": json.loads(_answer("존재하는 근거"))},
        {"question": "심볼 없음", "answer": json.loads(_answer("없는 심볼"))},
        {"question": "경로 없음", "answer": json.loads(_answer("없는 경로"))},
    ]
    qa_items[1]["answer"]["evidence"][0]["symbol"] = "MissingSymbol"
    qa_items[2]["answer"]["evidence"][0]["path"] = "missing.py"

    checks = verify_evidence(qa_items, tmp_path)
    assert checks[0]["evidence_check"] == {
        "path_exists": True, "symbol_found": True,
    }
    assert checks[1]["evidence_check"] == {
        "path_exists": True, "symbol_found": False,
    }
    assert checks[2]["evidence_check"] == {
        "path_exists": False, "symbol_found": None,
    }


def test_acquire_calls_in_order_filters_and_saves_only_run_memory(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    orch = Orchestrator(cfg, auto=True)
    _write_evidence_repo(tmp_path)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    monkeypatch.setattr(knot, "save", lambda *a, **k: pytest.fail("QA를 knot에 저장하면 안 됨"))
    calls = []
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": "첫 질문?"},
        {"category": "locating", "question": "둘째 질문?"},
    ], ensure_ascii=False))
    answer_results = [
        BackendResult("mock", True, _answer("유효한 조사 결과")),
        BackendResult("mock", True, _answer("근거 없는 결과", evidence=False)),
    ]

    def fake_call(worker, task, task_kind="general", extra_context="", cwd=None,
                  read_only=False):
        calls.append((worker, task, task_kind, cwd, read_only))
        return question_result

    def fake_parallel(specs):
        calls.extend((spec.worker, spec.task, spec.task_kind, spec.cwd, spec.read_only)
                     for spec in specs)
        return answer_results

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orch, "call_workers_parallel", fake_parallel)
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
    assert saved["dropped"] == []
    assert saved["qa_items"][0]["verdict"] == "confirmed"
    assert saved["qa_items"][0]["evidence_check"] == {
        "path_exists": True, "symbol_found": True,
    }


def test_acquire_saves_dropped_reason_and_never_writes_knot(tmp_path, monkeypatch):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    _write_evidence_repo(tmp_path)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    monkeypatch.setattr(knot, "save", lambda *a, **k: pytest.fail("QA knot 저장 금지"))
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": "존재하는 근거?"},
        {"category": "locating", "question": "사라진 근거?"},
    ], ensure_ascii=False))
    answer_results = [
        BackendResult("mock", True, _answer("확인된 답")),
        BackendResult("mock", True, _answer("폐기할 답")),
    ]

    def fake_call(*args, **kwargs):
        return question_result

    def fake_parallel(specs):
        results = []
        for result in answer_results:
            if "폐기할 답" not in result.text:
                results.append(result)
                continue
            payload = json.loads(result.text)
            payload["evidence"][0]["path"] = "missing.py"
            results.append(BackendResult("mock", True, json.dumps(payload, ensure_ascii=False)))
        return results

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orch, "call_workers_parallel", fake_parallel)
    context, qa_items = orch.acquire_preflight(
        "이슈", "claude-main", ["codex-main"], qa_count=2, workdir=str(tmp_path))

    assert len(qa_items) == 1 and "확인된 답" in context and "폐기할 답" not in context
    saved = json.loads((orch.run_dir / "acquire.json").read_text(encoding="utf-8"))
    assert saved["qa_items"][0]["verdict"] == "confirmed"
    assert saved["dropped"][0]["verdict"] == "contradicted"
    assert saved["dropped"][0]["reason"] == "evidence path does not exist"
    assert saved["dropped"][0]["evidence_check"]["path_exists"] is False


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_acquire_recheck_failure_keeps_existing_flow(tmp_path, monkeypatch, failure):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": "질문?"},
    ], ensure_ascii=False))
    answer_results = [
        BackendResult("mock", True, _answer("기존 QA 답")),
    ]
    calls = []

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return question_result

    def fake_parallel(specs):
        calls.extend((spec, {}) for spec in specs)
        return answer_results

    def fake_verify(*args, **kwargs):
        if failure == "exception":
            raise OSError("읽기 실패")
        return []

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orch, "call_workers_parallel", fake_parallel)
    monkeypatch.setattr(orchestrator, "verify_evidence", fake_verify)
    context, qa_items = orch.acquire_preflight(
        "이슈", "claude-main", ["codex-main"], workdir=str(tmp_path))

    assert len(calls) == 2
    assert len(qa_items) == 1 and "기존 QA 답" in context
    saved = json.loads((orch.run_dir / "acquire.json").read_text(encoding="utf-8"))
    assert saved["qa_items"] == qa_items and saved["dropped"] == []


def test_acquire_answerers_run_parallel_keep_question_order_and_read_only(
        tmp_path, monkeypatch):
    orch, captured_specs = _parallel_acquire_orch(tmp_path, monkeypatch)
    _write_evidence_repo(tmp_path)
    question_names = ["느린 첫 질문", "빠른 둘째 질문", "중간 셋째 질문"]
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": name}
        for name in question_names
    ], ensure_ascii=False))
    monkeypatch.setattr(orch, "call_worker", lambda *args, **kwargs: question_result)

    lock = threading.Lock()
    all_entered = threading.Event()
    active = maximum = 0
    read_only_values = []

    def backend(name, spec, prompt, **kwargs):
        nonlocal active, maximum
        question = next(value for value in question_names if value in prompt)
        with lock:
            active += 1
            maximum = max(maximum, active)
            read_only_values.append(kwargs.get("read_only"))
            if active == len(question_names):
                all_entered.set()
        assert all_entered.wait(1.0)
        time.sleep({question_names[0]: 0.08, question_names[1]: 0.01,
                    question_names[2]: 0.04}[question])
        with lock:
            active -= 1
        return BackendResult(name, True, _answer(f"{question}의 답"))

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    _, qa_items = orch.acquire_preflight(
        "병렬 이슈", "claude-main", ["codex-main"], qa_count=3,
        workdir=str(tmp_path))

    assert maximum == 3
    assert [item["question"]["question"] for item in qa_items] == question_names
    assert [item["answer"]["answer"] for item in qa_items] == [
        f"{name}의 답" for name in question_names
    ]
    assert len(captured_specs) == 3
    assert all(spec.read_only is True for spec in captured_specs)
    assert read_only_values == [True, True, True]


def test_acquire_answerer_failure_slot_keeps_other_qa(tmp_path, monkeypatch):
    orch, _ = _parallel_acquire_orch(tmp_path, monkeypatch)
    _write_evidence_repo(tmp_path)
    question_names = ["첫 질문", "실패 질문", "셋째 질문"]
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": name}
        for name in question_names
    ], ensure_ascii=False))
    monkeypatch.setattr(orch, "call_worker", lambda *args, **kwargs: question_result)

    def backend(name, spec, prompt, **kwargs):
        if "실패 질문" in prompt:
            raise RuntimeError("answerer boom")
        question = next(value for value in question_names if value in prompt)
        return BackendResult(name, True, _answer(f"{question}의 답"))

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    _, qa_items = orch.acquire_preflight(
        "부분 성공 이슈", "claude-main", ["codex-main"], qa_count=3,
        workdir=str(tmp_path))

    assert [item["question"]["question"] for item in qa_items] == ["첫 질문", "셋째 질문"]
    assert [item["answer"]["answer"] for item in qa_items] == ["첫 질문의 답", "셋째 질문의 답"]


@pytest.mark.parametrize("enabled", [False, True])
def test_acquire_parallel_on_off_returns_same_ordered_result(tmp_path, monkeypatch, enabled):
    orch, _ = _parallel_acquire_orch(tmp_path, monkeypatch, enabled=enabled)
    _write_evidence_repo(tmp_path)
    question_names = ["하나", "둘"]
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": name}
        for name in question_names
    ], ensure_ascii=False))
    monkeypatch.setattr(orch, "call_worker", lambda *args, **kwargs: question_result)

    def backend(name, spec, prompt, **kwargs):
        question = next(value for value in question_names if f"질문: {value}" in prompt)
        return BackendResult(name, True, _answer(f"답-{question}"))

    monkeypatch.setattr(orchestrator, "run_backend", backend)
    _, qa_items = orch.acquire_preflight(
        "동일 결과 이슈", "claude-main", ["codex-main"], qa_count=2,
        workdir=str(tmp_path))

    assert [item["answer"]["answer"] for item in qa_items] == ["답-하나", "답-둘"]


def test_acquire_parallel_run_aborted_propagates(tmp_path, monkeypatch):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": "중단 질문"},
    ], ensure_ascii=False))
    monkeypatch.setattr(orch, "call_worker", lambda *args, **kwargs: question_result)

    def abort(specs):
        raise RunAborted("guard stop")

    monkeypatch.setattr(orch, "call_workers_parallel", abort)
    with pytest.raises(RunAborted, match="guard stop"):
        orch.acquire_preflight("이슈", "claude-main", ["codex-main"])


def test_acquire_answerer_batch_failure_is_fail_safe(tmp_path, monkeypatch):
    orch = Orchestrator(_cfg(tmp_path), auto=True)
    monkeypatch.setattr(usage, "check_backend", lambda cfg, backend: _verdict())
    question_result = BackendResult("mock", True, json.dumps([
        {"category": "mechanism", "question": "질문"},
    ], ensure_ascii=False))
    monkeypatch.setattr(orch, "call_worker", lambda *args, **kwargs: question_result)

    def fail(specs):
        raise OSError("batch failed")

    monkeypatch.setattr(orch, "call_workers_parallel", fail)
    assert orch.acquire_preflight("이슈", "claude-main", ["codex-main"]) == ("", [])
    saved = json.loads((orch.run_dir / "acquire.json").read_text(encoding="utf-8"))
    assert saved["qa_items"] == [] and len(saved["questions"]) == 1


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
