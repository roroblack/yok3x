from __future__ import annotations

import json

import pytest

from yok3x import review_protocol
from yok3x.backends import BackendResult
from yok3x.config import Config, scaffold
from yok3x.orchestrator import Orchestrator, StepLog, _load_replay_steps


@pytest.mark.parametrize("adversarial", [False, True])
def test_reviewer_prompt_includes_structured_contract(tmp_path, monkeypatch, adversarial):
    scaffold(tmp_path, use_mock=True)
    cfg = Config.load(tmp_path)
    cfg.yok3x["adversarial_review"] = adversarial
    orch = Orchestrator(cfg, auto=True)
    prompts = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        if task_kind == "critic":
            prompts.append(task)
        orch._step_i += 1
        text = "SCORE: 9\nlooks good" if task_kind == "critic" else "artifact"
        score = 9.0 if task_kind == "critic" else None
        orch.steps.append(StepLog(orch._step_i, worker, task_kind, "done",
                                  summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    orch.run_producer_reviewer("review this", "claude-main", "codex-critic", max_rounds=1)

    assert len(prompts) == 1
    prompt = prompts[0]
    assert review_protocol.PROTOCOL_VERSION in prompt
    assert "SCORE: <0-10>" in prompt
    assert '"defects"' in prompt
    assert "critical|high|medium|low" in prompt
    assert "defects: []" in prompt
    assert "evidence" in prompt and "fix" in prompt
    if adversarial:
        assert "반례" in prompt
        assert "미검증 가정" in prompt
        assert "보안 결함" in prompt


def test_revise_prompt_still_receives_legacy_round_feedback(tmp_path, monkeypatch):
    scaffold(tmp_path, use_mock=True)
    orch = Orchestrator(Config.load(tmp_path), auto=True)
    calls = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        calls.append((task_kind, task, extra_context))
        orch._step_i += 1
        if task_kind == "critic":
            round_no = sum(kind == "critic" for kind, _, _ in calls)
            text = "SCORE: 5\nlegacy defect" if round_no == 1 else "SCORE: 9\nfixed"
            score = 5.0 if round_no == 1 else 9.0
        else:
            text, score = "artifact", None
        orch.steps.append(StepLog(orch._step_i, worker, task_kind, "done",
                                  summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    orch.run_producer_reviewer("review this", "claude-main", "codex-critic", max_rounds=2)

    revise = next(call for call in calls if call[0] == "revise")
    assert revise[1].startswith("review this\n\n")
    assert "SCORE: 5\nlegacy defect" in revise[2]


def _run_review_for_issues_sig(tmp_path, monkeypatch, review_text):
    scaffold(tmp_path, use_mock=True)
    orch = Orchestrator(Config.load(tmp_path), auto=True)
    observed = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        orch._step_i += 1
        text = review_text if task_kind == "critic" else "artifact"
        score = 5.0 if task_kind == "critic" else None
        orch.steps.append(StepLog(orch._step_i, worker, task_kind, "done",
                                  summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    def fake_new_evidence(previous, current):
        observed.append(current)
        return False, "test_stop"

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orch, "_new_evidence", fake_new_evidence)
    orch.run_producer_reviewer("review this", "claude-main", "codex-critic", max_rounds=1)
    return orch, observed[0]


def test_structured_review_uses_canonical_issues_signature_and_records_source(tmp_path, monkeypatch):
    response = ('{"protocol_version":"review-v1","score":5,"defects":['
                '{"severity":"high","description":"A bug","evidence":"x","fix":"y"},'
                '{"severity":"low","description":"B bug"}]}')
    orch, evidence = _run_review_for_issues_sig(tmp_path, monkeypatch, response)

    assert evidence["issues_sig"] == ("high:a bug", "low:b bug")
    assert orch._calib_rounds[0]["issues_sig_source"] == "structured"
    record = json.loads((orch.cfg.paths.runs.parent / "calibration.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    assert "issues_sig_source" not in record


def test_structured_review_signature_is_order_independent_but_content_sensitive(tmp_path, monkeypatch):
    first = ('{"protocol_version":"review-v1","score":5,"defects":['
             '{"severity":"high","description":"A bug"},'
             '{"severity":"low","description":"B bug"}]}')
    second = ('{"defects":[{"description":" b bug ","severity":"low"},'
              '{"description":" a bug ","severity":"high"}],'
              '"score":5,"protocol_version":"review-v1"}')
    changed = ('{"protocol_version":"review-v1","score":5,"defects":['
               '{"severity":"critical","description":"A bug"},'
               '{"severity":"low","description":"B bug"}]}')

    _, first_evidence = _run_review_for_issues_sig(tmp_path / "first", monkeypatch, first)
    _, second_evidence = _run_review_for_issues_sig(tmp_path / "second", monkeypatch, second)
    _, changed_evidence = _run_review_for_issues_sig(tmp_path / "changed", monkeypatch, changed)
    assert first_evidence["issues_sig"] == second_evidence["issues_sig"]
    assert first_evidence["issues_sig"] != changed_evidence["issues_sig"]


def test_malformed_structured_review_falls_back_to_legacy_issues_signature(tmp_path, monkeypatch):
    text = 'SCORE: 5\n- legacy defect'
    orch, evidence = _run_review_for_issues_sig(tmp_path, monkeypatch, text + ' {"protocol_version":"review-v1"')

    assert evidence["issues_sig"] == orch._defect_sig(text + ' {"protocol_version":"review-v1"')
    assert orch._calib_rounds[0]["issues_sig_source"] == "legacy_text"


def _round_response(defects, score=5):
    return json.dumps({
        "protocol_version": review_protocol.PROTOCOL_VERSION,
        "score": score,
        "defects": defects,
    })


def _run_rounds(tmp_path, monkeypatch, reviews, *, artifacts=None,
                verify_results=None, escalate=None, reviewer="codex-critic"):
    scaffold(tmp_path, use_mock=True)
    orch = Orchestrator(Config.load(tmp_path), auto=True)
    orch.escalate = escalate or {}
    calls = []
    review_index = 0
    artifact_index = 0

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        nonlocal review_index, artifact_index
        calls.append((worker, task_kind))
        orch._step_i += 1
        if task_kind == "critic":
            text = reviews[review_index]
            review_index += 1
            score = 5.0
        else:
            text = (artifacts or ["artifact"] * len(reviews))[artifact_index]
            artifact_index += 1
            score = None
        orch.steps.append(StepLog(orch._step_i, worker, task_kind, "done",
                                  summary=text, score=score))
        return BackendResult(backend=worker, ok=True, text=text)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    if verify_results is not None:
        orch.verify_cmd = "mock verify"
        results = iter(verify_results)
        monkeypatch.setattr(orch, "_run_round_verify",
                            lambda artifact, rnd: (*next(results),))
    orch.run_producer_reviewer("review this", "claude-main", reviewer,
                               max_rounds=len(reviews), pass_score=8.0)
    return orch, calls


def test_s4_identical_structured_defects_stall_even_when_reordered(tmp_path, monkeypatch):
    defect = {"severity": "high", "description": "  same   defect  "}
    reordered = [{"description": "same defect", "severity": "high"}]
    orch, calls = _run_rounds(
        tmp_path, monkeypatch,
        [_round_response([defect]), _round_response(reordered)])

    assert orch.stop_reason == "no_new_evidence"
    assert [kind for _, kind in calls] == ["build", "critic", "revise", "critic"]
    assert [row["issues_sig_source"] for row in orch._calib_rounds] == ["structured", "structured"]


def test_s4_changed_structured_defects_retry_with_issues_changed(tmp_path, monkeypatch):
    orch, calls = _run_rounds(
        tmp_path, monkeypatch,
        [_round_response([{"severity": "high", "description": "first"}]),
         _round_response([{"severity": "high", "description": "first"},
                          {"severity": "low", "description": "new"}])],
        artifacts=["same artifact", "same artifact"])

    assert orch.stop_reason == "max_rounds"
    assert len([kind for _, kind in calls if kind == "critic"]) == 2
    assert orch._calib_rounds[0]["issues_sig_source"] == "structured"
    assert orch._calib_rounds[1]["issues_sig_source"] == "structured"


@pytest.mark.parametrize("axis", ["artifact", "verify"])
def test_s4_artifact_and_verify_are_independent_evidence_axes(tmp_path, monkeypatch, axis):
    kwargs = {}
    if axis == "artifact":
        kwargs["artifacts"] = ["artifact one", "artifact two"]
    else:
        kwargs["verify_results"] = [(False, "first", "original_tree"),
                                     (True, "second", "original_tree")]
    orch, calls = _run_rounds(
        tmp_path / axis, monkeypatch,
        [_round_response([{"severity": "high", "description": "same"}])] * 2,
        **kwargs)

    assert orch.stop_reason == "max_rounds"
    assert len([kind for _, kind in calls if kind == "critic"]) == 2


def test_s4_structured_and_legacy_rounds_both_use_their_declared_path(tmp_path, monkeypatch):
    structured = _round_response([{"severity": "high", "description": "same defect"}])
    legacy = "SCORE: 5\n- same defect"
    orch, calls = _run_rounds(tmp_path, monkeypatch, [structured, legacy])

    assert orch.stop_reason == "max_rounds"
    assert [row["issues_sig_source"] for row in orch._calib_rounds] == ["structured", "legacy_text"]
    assert len([kind for _, kind in calls if kind == "critic"]) == 2


def test_s4_escalated_reviewer_still_parses_structured_json(tmp_path, monkeypatch):
    review = _round_response([{"severity": "medium", "description": "same"}])
    orch, calls = _run_rounds(
        tmp_path, monkeypatch, [review, review],
        escalate={"after_round": 1, "if_score_below": 6, "to_reviewer": "gemini"})

    critic_workers = [worker for worker, kind in calls if kind == "critic"]
    assert critic_workers == ["codex-critic", "gemini"]
    assert [row["issues_sig_source"] for row in orch._calib_rounds] == ["structured", "structured"]
    assert [row["reviewer"] for row in orch._calib_rounds] == ["codex-critic", "gemini"]


def test_s4_old_step_without_issues_sig_source_replays_as_legacy_compatible(tmp_path):
    step = {
        "worker": "codex-critic", "task_kind": "critic", "task": "review",
        "call_key": "old-call", "ok": True, "error": None,
        "text": "SCORE: 5\n- old defect", "score": 5.0, "checklist": [],
        "usage": {"cost_usd": 0, "total_tokens": 1, "duration_ms": 1},
    }
    (tmp_path / "step_01_codex-critic.json").write_text(
        json.dumps(step), encoding="utf-8")

    cache, reason = _load_replay_steps(tmp_path)
    assert "old-call" in cache
    assert "issues_sig_source" not in cache["old-call"]
    assert reason
