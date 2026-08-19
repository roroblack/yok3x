from __future__ import annotations

import pytest

from yok3x import review_protocol
from yok3x.backends import BackendResult
from yok3x.config import Config, scaffold
from yok3x.orchestrator import Orchestrator, StepLog


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
