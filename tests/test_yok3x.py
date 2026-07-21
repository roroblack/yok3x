"""yok3x 회귀 잠금 테스트.

손으로 매번 확인하던 것을 자동화한다(RULE §5: 실행해서 확인). mock 백엔드 위에서
외부 도구 없이 결정적으로 돈다. 실행: 프로젝트 루트에서 `pytest` 또는 `python -m pytest`.
verify_cmd 게이트에 `pytest -q`를 걸면 프로젝트가 자기 자신을 dogfooding하게 된다.
"""
from __future__ import annotations

import copy
import json

import subprocess
from pathlib import Path

import pytest

from yok3x import __version__, backends, calibration, limits, matview, orchestrator, usage
from yok3x.backends import BackendResult, run_backend
from yok3x.config import DEFAULT_YOK3X, Config, scaffold
from yok3x.orchestrator import Orchestrator, run_task_file


@pytest.fixture
def mock_root(tmp_path):
    """mock 백엔드로 초기화된 격리 작업 디렉터리."""
    scaffold(tmp_path, use_mock=True)
    return tmp_path


# --------------------------------------------------------------- ① deepcopy 격리
def test_scaffold_mock_does_not_pollute_global(tmp_path):
    before = DEFAULT_YOK3X["workers"]["claude-main"]["backend"]
    scaffold(tmp_path, use_mock=True)
    assert DEFAULT_YOK3X["workers"]["claude-main"]["backend"] == before == "claude"


def test_partial_config_load_does_not_alias_global(tmp_path):
    # workers 키 없는 부분 설정을 로드해도 중첩 dict가 전역을 가리키면 안 된다.
    (tmp_path / "yok3x.json").write_text(
        json.dumps({"flavor": "claude-orchestrator"}), encoding="utf-8")
    cfg = Config.load(tmp_path)
    cfg.yok3x["workers"]["claude-main"]["backend"] = "MUTATED"
    assert DEFAULT_YOK3X["workers"]["claude-main"]["backend"] == "claude"


# --------------------------------------------------------------- ② 버전 일관성
def test_version_is_single_source(mock_root):
    cfg = Config.load(mock_root)
    banner = matview.render(cfg).splitlines()[0]
    assert __version__ in banner
    for stale in ("v2.2", "v3.0"):
        assert stale not in banner


# --------------------------------------------------------------- ③ 스톨 결함 서명
def test_defect_sig_ignores_order_and_numbering():
    sig = Orchestrator._defect_sig
    r1 = "SCORE: 6\n- 널 체크 누락 (config.py)\n- 예외 처리 없음\n* 테스트 부재"
    r2 = "SCORE: 6\n1. 예외 처리 없음\n2) 널 체크 누락 (config.py)\n- 테스트 부재"
    assert sig(r1) == sig(r2)                 # 순서·번호만 다름 → 동일 서명


def test_defect_sig_distinguishes_and_handles_empty():
    sig = Orchestrator._defect_sig
    diff = "SCORE: 8\n- 전부 반영됨, 통과"
    assert sig("SCORE: 6\n- 널 체크 누락") != sig(diff)
    assert sig("") == ()                      # 빈 응답
    assert sig("SCORE: 7") == ()              # 점수만 있는 리뷰


# --------------------------------------------------------- 3패턴 mock end-to-end
@pytest.mark.parametrize("spec", [
    {"pattern": "producer-reviewer", "task": "t", "producer": "claude-main",
     "reviewer": "codex-critic", "max_rounds": 2, "pass_score": 8.0},
    {"pattern": "pipeline", "task": "t", "stages": [
        {"worker": "claude-main", "kind": "build", "task": "설계"},
        {"worker": "codex-main", "kind": "build", "task": "구현"},
        {"worker": "codex-critic", "kind": "review", "task": "리뷰"}]},
    {"pattern": "fanout-fanin", "task": "t",
     "workers": ["claude-main", "codex-main", "gemini"], "join_worker": "claude-main"},
])
def test_three_patterns_run_to_done(mock_root, spec):
    cfg = Config.load(mock_root)
    tf = mock_root / "task.json"
    tf.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert run_task_file(cfg, tf, auto=True) == "done"


# ----------------------------------------------- 작업별 에이전트 배치 override
def test_worker_merges_partial_run_override_without_mutating_global(mock_root):
    cfg = Config.load(mock_root)
    before = copy.deepcopy(cfg.yok3x["workers"])
    o = Orchestrator(cfg, auto=True)
    o.agents_override = {"claude-main": {"backend": "codex", "effort": "high"}}

    worker = o._worker("claude-main")

    assert worker["backend"] == "codex"
    assert worker["effort"] == "high"
    assert worker["role"] == before["claude-main"]["role"]   # 부분 override: 나머지는 전역 상속
    assert cfg.yok3x["workers"] == before                       # 얕은 복사 병합: 전역 미오염


def test_worker_without_override_is_same_as_global(mock_root):
    cfg = Config.load(mock_root)
    o = Orchestrator(cfg, auto=True)

    assert o._worker("claude-main") == cfg.worker("claude-main")
    assert o._worker("claude-main") is not cfg.worker("claude-main")


def test_run_task_file_applies_agents_without_polluting_config(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    before = copy.deepcopy(cfg.yok3x["workers"])
    agents = {"claude-main": {"backend": "codex", "model": "gpt-test", "effort": "high"}}
    tf = mock_root / "task-agents.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t",
                              "producer": "claude-main", "reviewer": "codex-critic",
                              "agents": agents}), encoding="utf-8")
    seen = {}

    def fake_run(self, *args, **kwargs):
        seen["agents"] = self.agents_override
        seen["worker"] = self._worker("claude-main")

    monkeypatch.setattr(Orchestrator, "run_producer_reviewer", fake_run)

    assert run_task_file(cfg, tf, auto=True) == "done"
    assert seen["agents"] == agents
    assert seen["worker"]["backend"] == "codex"
    assert seen["worker"]["role"] == before["claude-main"]["role"]
    assert cfg.yok3x["workers"] == before


@pytest.mark.parametrize(("agents", "error"), [
    ({"없는-워커": {"backend": "codex"}}, "없는 워커"),
    ({"claude-main": {"backend": "없는-backend"}}, "잘못된 backend"),
    ({"claude-main": {"effort": "ultra"}}, "effort 값 오류"),
])
def test_validate_task_spec_rejects_bad_agent_override(mock_root, agents, error):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "t", "agents": agents}

    assert error in gs._validate_task_spec(spec, cfg)


def test_override_backend_is_used_for_actual_worker_call(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False
    calls = []

    def fake_backend(name, backend_spec, prompt, **kwargs):
        calls.append((name, backend_spec, kwargs))
        return BackendResult(backend=name, ok=True, text="ok")

    monkeypatch.setattr(orchestrator, "run_backend", fake_backend)
    o = Orchestrator(cfg, auto=True)
    o.agents_override = {"claude-main": {"backend": "codex", "model": "gpt-test",
                                          "effort": "high"}}

    assert o.call_worker("claude-main", "t").ok
    assert calls[0][0] == "codex"
    assert calls[0][1] is cfg.backends["codex"]
    assert calls[0][2]["model"] == "gpt-test"
    assert calls[0][2]["effort"] == "high"


def test_task_agents_survive_save_load_roundtrip(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    agents = {"claude-main": {"backend": "codex", "effort": "high"}}
    spec = {"pattern": "producer-reviewer", "task": "배치 왕복", "agents": agents}

    saved = gs._save_task(cfg, "배치 왕복", spec)
    loaded = gs._load_task(cfg, saved["name"])

    assert saved["ok"] and loaded["ok"]
    assert loaded["spec"]["agents"] == agents


# ----------------------------------------------- 작업(task)별 콘솔: label 흐름
def test_run_label_flows_to_status_and_recent(mock_root):
    from yok3x.guiserver import _recent_runs
    cfg = Config.load(mock_root)
    tf = mock_root / "t.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t", "label": "슬러그 함수",
                              "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1},
                             ensure_ascii=False), encoding="utf-8")
    run_task_file(cfg, tf, auto=True)
    runs = _recent_runs(cfg, 10)
    assert runs and runs[0]["label"] == "슬러그 함수"                    # label 저장·노출
    tf2 = mock_root / "task-foo.json"
    tf2.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t2",
                               "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1},
                              ensure_ascii=False), encoding="utf-8")
    run_task_file(cfg, tf2, auto=True)
    assert "task-foo" in {r["label"] for r in _recent_runs(cfg, 10)}    # 라벨 없으면 파일명 폴백


def test_inline_spec_label_defaults_untitled(mock_root):
    from yok3x.guiserver import _write_inline_spec, _recent_runs
    cfg = Config.load(mock_root)
    tf = _write_inline_spec(cfg, {"pattern": "producer-reviewer", "task": "x",
                                  "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1})
    assert json.loads(tf.read_text(encoding="utf-8"))["label"] == ""    # 임시파일명 폴백 안 함
    run_task_file(cfg, tf, auto=True)
    assert _recent_runs(cfg, 10)[0]["label"] == ""                      # 무제목(빈 라벨)


# ----------------------------------------------- 조건부 라우팅(에스컬레이션)
def test_conditional_routing_escalates_on_low_score(mock_root, monkeypatch):
    from yok3x.backends import BackendResult
    cfg = Config.load(mock_root)
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda n, s, p, cwd=None, model=None, effort=None:
                        BackendResult(backend=n, ok=True, text="SCORE: 3\n결함 반복"))
    o = orchestrator.Orchestrator(cfg, auto=True)
    o.escalate = {"after_round": 2, "if_score_below": 6, "to_reviewer": "gemini"}
    o.run_producer_reviewer("계산기", "claude-main", "codex-critic", max_rounds=4)
    workers = [s.worker for s in o.steps]
    assert "codex-critic" in workers            # 전환 전 리뷰어
    assert "gemini" in workers                  # 낮은 점수 지속 → 리뷰어가 gemini로 에스컬레이션(1회)


def test_conditional_routing_invalid_target_fails(mock_root):
    from yok3x.orchestrator import RunAborted
    cfg = Config.load(mock_root)
    o = orchestrator.Orchestrator(cfg, auto=True)
    o.escalate = {"to_reviewer": "nonexistent-worker"}   # 오타/부재
    with pytest.raises(RunAborted):                       # 조용한 폴백 아니라 명확한 실패(codex 리뷰)
        o.run_producer_reviewer("t", "claude-main", "codex-critic", max_rounds=2)


# ----------------------------------------------- E few-shot 예시 주입
@pytest.mark.parametrize("kind,expected", [
    ("build", True), ("revise", True), ("general", False), ("critic", False)])
def test_examples_injected_only_for_producer_kinds(mock_root, kind, expected):
    """E: 예시는 build/revise(Resolver/생산자)에만. general(ACQUIRE Q/A)·critic엔 주입 금지."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.examples = "예시: def foo(): return 42"
    spec = o.prepare_call("claude-main", "작업", task_kind=kind)
    assert ("[예시]" in spec.prompt) is expected


def test_examples_char_limit_applied(mock_root):
    cfg = Config.load(mock_root)
    cfg.yok3x["examples_max_chars"] = 100
    o = Orchestrator(cfg, auto=True)
    o.examples = "X" * 5000
    spec = o.prepare_call("claude-main", "작업", task_kind="build")
    assert spec.prompt.count("X") <= 120        # clip 상한(head+tail) 내


def test_examples_spec_accepts_list_and_string(mock_root):
    from yok3x.orchestrator import _run_task_file  # noqa: F401  (스펙 읽기 경로 확인용)
    o = Orchestrator(Config.load(mock_root), auto=True)
    # 리스트→빈 줄로 결합되는지(직접 필드 세팅은 _run_task_file이 함)
    o.examples = "\n\n".join(["ex1", "ex2"])
    spec = o.prepare_call("claude-main", "작업", task_kind="build")
    assert "ex1" in spec.prompt and "ex2" in spec.prompt


# ----------------------------------------------- T1 자동 트리아지(추천 전용)
def test_triage_scale_is_not_risk_high_impact_stays_cautious():
    """규모≠위험: '한 줄 배포'는 저복잡이어도 고영향 → api·검토생략 금지."""
    from yok3x import triage
    r = triage.estimate_execution({
        "task": "deploy.yaml replicas를 3으로", "workdir": "/prod",
        "materialize": {"enabled": True}, "verify_cmd": "pytest"})
    assert r["tier"] == "api" and r["skip_review"] is False


def test_triage_skip_review_only_when_low_impact_verifiable_low_complexity():
    from yok3x import triage
    ok = triage.estimate_execution({"task": "짧은 작업", "verify_cmd": "pytest"})
    assert ok["pattern"] == "direct" and ok["skip_review"] is True
    # verify 없으면 검토 생략 절대 불가(객관 신호 없음)
    no_v = triage.estimate_execution({"task": "짧은 작업"})
    assert no_v["skip_review"] is False


def test_triage_is_recommendation_only_shape():
    from yok3x import triage
    r = triage.estimate_execution({"task": "x", "pattern": "producer-reviewer", "max_rounds": 3})
    assert set(r) == {"pattern", "tier", "max_rounds", "skip_review",
                      "confidence", "axes", "reasons"}
    assert 1 <= r["max_rounds"] <= 2         # 보수적 상한(escalate가 실제로 올린다)
    assert isinstance(r["reasons"], list) and r["reasons"]


# ----------------------------------------------- 심판 캘리브레이션 원자료
def test_calibration_make_record_rejects_unknown_and_fills_missing():
    with pytest.raises(TypeError, match="verify_passed"):
        calibration.make_record(score=7, verify_passed=True)

    rec = calibration.make_record(score=7, verify_ok=True, verify_scope="candidate")
    assert rec["score"] == 7 and rec["verify_ok"] is True
    assert rec["verify_scope"] == "candidate"
    assert rec["reviewer"] is None and rec["round"] is None and rec["gate_mode"] is None


def test_calibration_labels_only_candidate_verify_scope():
    candidate = calibration.make_record(
        score=9, verify_ok=True, verify_scope="candidate")
    original = calibration.make_record(
        score=2, verify_ok=False, verify_scope="original_tree")
    legacy = calibration.make_record(score=5, verify_ok=True)

    assert calibration._labeled([candidate, original, legacy]) == [candidate]


def test_calibration_summarize_excludes_original_tree_labels():
    records = [
        calibration.make_record(
            score=9, verify_ok=True, verify_scope="original_tree"),
        calibration.make_record(
            score=2, verify_ok=False, verify_scope="original_tree"),
    ]

    summary = calibration.summarize(records)
    assert summary["n_total"] == 2 and summary["n_labeled"] == 0
    assert summary["pass_rate"] is None and summary["score_verify_corr"] is None
    assert summary["confusion"]["n"] == 0


@pytest.mark.parametrize(("mode", "has_verify", "verify_ok", "score", "passed"), [
    ("strict", True, True, 9.0, True),
    ("strict", True, True, 7.9, False),
    ("strict", True, False, 9.0, False),
    ("strict", False, None, 9.0, True),
    ("strict", False, None, 7.9, False),
    ("advisory", True, True, 7.9, True),
    ("advisory", True, False, 9.0, False),
])
def test_evaluate_score_gate_matrix(mode, has_verify, verify_ok, score, passed):
    gate = orchestrator.evaluate_score_gate(
        mode, has_verify_cmd=has_verify, verify_ok=verify_ok,
        score=score, threshold=8.0)

    assert gate["passed"] is passed
    assert gate["mode"] == mode and gate["threshold"] == 8.0
    assert gate["verify_ok"] is (bool(verify_ok) if has_verify else None)


def test_evaluate_score_gate_advisory_low_score_requires_review():
    gate = orchestrator.evaluate_score_gate(
        "advisory", has_verify_cmd=True, verify_ok=True,
        score=7.9, threshold=8.0)

    assert gate["passed"] is True
    assert gate["review_required"] is True
    assert gate["reason"] == "score_below_threshold_review_required"


@pytest.mark.parametrize(("mode", "has_verify"), [
    ("advisory", False),
    ("auto", True),
])
def test_evaluate_score_gate_rejects_bad_config(mode, has_verify):
    with pytest.raises(orchestrator.RunAborted) as caught:
        orchestrator.evaluate_score_gate(
            mode, has_verify_cmd=has_verify, verify_ok=True,
            score=9.0, threshold=8.0)

    assert caught.value.cause == "config_error"


@pytest.mark.parametrize("mode,verify_cmd", [("advisory", ""), ("auto", "verify")])
def test_producer_reviewer_rejects_gate_config_before_worker(
        mock_root, monkeypatch, mode, verify_cmd):
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.score_gate_mode = mode
    o.verify_cmd = verify_cmd
    monkeypatch.setattr(
        o, "call_worker",
        lambda *a, **k: pytest.fail("설정 오류에서 워커를 호출하면 안 됨"))

    with pytest.raises(orchestrator.RunAborted) as caught:
        o.run_producer_reviewer("t", "claude-main", "codex-critic")

    assert caught.value.cause == "config_error"


def _run_gate_case(mock_root, monkeypatch, mode, score):
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.score_gate_mode = mode
    o.verify_cmd = "verify sentinel"
    calls = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        calls.append(task_kind)
        o._step_i += 1
        value = score if task_kind == "critic" else None
        text = f"SCORE: {score}\nreview" if task_kind == "critic" else "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=value))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", lambda: (True, "ok"))
    o.run_producer_reviewer(
        "gate status", "claude-main", "codex-critic",
        max_rounds=2, pass_score=8.0)
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in
               (o.cfg.paths.runs.parent / "calibration.jsonl").read_text(
                   encoding="utf-8").splitlines()]
    return calls, status, records


def test_advisory_low_score_passes_once_and_persists_gate(mock_root, monkeypatch):
    calls, status, records = _run_gate_case(mock_root, monkeypatch, "advisory", 5.0)

    assert calls == ["build", "critic"]              # verify 첫 통과에서 조기 종료
    assert status["state"] == "done"                  # done 의미는 실행 완료로 유지
    assert status["gate"] == {
        "mode": "advisory", "passed": True, "verify_ok": True,
        "score": 5.0, "threshold": 8.0, "review_required": True,
        "reason": "score_below_threshold_review_required",
    }
    assert len(records) == 1 and records[0]["gate_mode"] == "advisory"


def test_strict_low_score_stays_rejected_and_persists_gate(mock_root, monkeypatch):
    calls, status, records = _run_gate_case(mock_root, monkeypatch, "strict", 5.0)

    assert calls == ["build", "critic", "revise", "critic"]
    assert status["state"] == "done"
    assert status["gate"]["mode"] == "strict"
    assert status["gate"]["passed"] is False
    assert status["gate"]["review_required"] is False
    assert all(record["gate_mode"] == "strict" for record in records)


def test_producer_failure_still_persists_unevaluated_gate(mock_root, monkeypatch):
    o = Orchestrator(Config.load(mock_root), auto=True)
    monkeypatch.setattr(
        o, "call_worker",
        lambda *a, **k: BackendResult(backend="mock", ok=False, text="producer failed"))

    o.run_producer_reviewer(
        "failed producer", "claude-main", "codex-critic", max_rounds=1)

    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert status["gate"]["mode"] == "strict"
    assert status["gate"]["passed"] is False
    assert status["gate"]["reason"] == "not_evaluated"


def test_calibration_logs_every_round_with_gate_context(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "sentinel verify"
    o.agents_override = {
        "claude-main": {"backend": "producer-backend", "effort": "high"},
        "codex-critic": {"backend": "reviewer-backend"},
    }
    scores = iter([3.0, 5.0, 9.0])
    verify_calls = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        o._step_i += 1
        if task_kind == "critic":
            score = next(scores)
            text = f"SCORE: {score}\nround-specific defect {score}"
        else:
            score, text = None, "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend=o._worker(worker)["backend"], ok=True, text=text)

    def fake_verify():
        verify_calls.append(True)
        return True, "ok"

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", fake_verify)
    o.run_producer_reviewer(
        "calibrate", "claude-main", "codex-critic", max_rounds=3, pass_score=8.0)

    path = cfg.paths.runs.parent / "calibration.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(verify_calls) == 3
    assert {r["run_id"] for r in records} == {o.run_id}
    assert [r["round"] for r in records] == [1, 2, 3]
    assert [r["score"] for r in records] == [3.0, 5.0, 9.0]
    assert all(r["reviewer"] == "reviewer-backend" for r in records)
    assert all(r["threshold"] == 8.0 for r in records)
    assert all(r["gate_mode"] == "strict" for r in records)
    assert [r["gate_pass"] for r in records] == [False, False, True]
    assert all(r["verify_ok"] is True for r in records)
    assert all(r["verify_scope"] == "original_tree" for r in records)
    # rounds=총 라운드 수(전 행 동일), round=인덱스 — 둘이 중복이면 안 된다(검토 수정).
    assert all(r["rounds"] == 3 for r in records)
    # 런 합계는 마지막 행에만 — 전 행에 반복하면 파일 합산이 과대계상된다(검토 수정).
    assert [r["issues"] is not None for r in records] == [False, False, True]
    assert sum(r["tokens"] or 0 for r in records) == (records[-1]["tokens"] or 0)


@pytest.mark.parametrize("adversarial", [False, True])
def test_reviewer_is_blind_to_verify_result(mock_root, monkeypatch, adversarial):
    cfg = Config.load(mock_root)
    cfg.yok3x["adversarial_review"] = adversarial
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "verify command sentinel"
    verify_sentinel = "VERIFY_OUTPUT_SENTINEL"
    calls = []

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        calls.append((task_kind, task, extra_context))
        o._step_i += 1
        score = 9.0 if task_kind == "critic" else None
        text = "SCORE: 9\nindependent review" if task_kind == "critic" else "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", lambda: (False, verify_sentinel))
    o.run_producer_reviewer(
        "blind review", "claude-main", "codex-critic", max_rounds=1)

    reviewer_calls = [(task, context) for kind, task, context in calls if kind == "critic"]
    assert len(reviewer_calls) == 1
    reviewer_prompt = "\n".join(reviewer_calls[0])
    assert verify_sentinel not in reviewer_prompt
    assert "verify command sentinel" not in reviewer_prompt
    assert "테스트/검증 결과" not in reviewer_prompt


def test_verify_failure_stays_hard_gate_and_reaches_next_producer(mock_root, monkeypatch):
    cfg = Config.load(mock_root)
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "verify command"
    verify_sentinel = "FAILURE_DIAGNOSTIC_SENTINEL"
    calls = []
    events = []
    verify_results = iter([(False, verify_sentinel), (True, "ok")])

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        calls.append((task_kind, task, extra_context))
        events.append(task_kind)
        o._step_i += 1
        score = 9.0 if task_kind == "critic" else None
        text = "SCORE: 9\nlooks good" if task_kind == "critic" else f"artifact {task_kind}"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    def fake_verify():
        events.append("verify")
        return next(verify_results)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", fake_verify)
    o.run_producer_reviewer(
        "hard gate", "claude-main", "codex-critic", max_rounds=2)

    assert [kind for kind, _, _ in calls] == ["build", "critic", "revise", "critic"]
    assert events == ["build", "verify", "critic", "revise", "verify", "critic"]
    revise_context = next(context for kind, _, context in calls if kind == "revise")
    assert verify_sentinel in revise_context
    assert all(verify_sentinel not in context for kind, _, context in calls if kind == "critic")

    path = cfg.paths.runs.parent / "calibration.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["score"] for record in records] == [9.0, 9.0]
    assert [record["verify_ok"] for record in records] == [False, True]
    assert [record["gate_pass"] for record in records] == [False, True]


def _candidate_verify_fixture(tmp_path, candidate="fixed"):
    """원본은 실패하고 app.txt 후보가 정확히 fixed일 때만 통과하는 실제 verify 환경."""
    import sys

    cfg = Config.load(tmp_path)
    workdir = tmp_path / "project"
    workdir.mkdir()
    app = workdir / "app.txt"
    app.write_text("broken", encoding="utf-8")
    (workdir / "check.py").write_text(
        "from pathlib import Path\n"
        "value = Path('app.txt').read_text(encoding='utf-8')\n"
        "raise SystemExit(0 if value == 'fixed' else 1)\n",
        encoding="utf-8",
    )
    orch = Orchestrator(cfg, auto=True)
    orch.workdir = str(workdir)
    orch.verify_cmd = f'"{sys.executable}" check.py'
    artifact = f"```file:app.txt\n{candidate}\n```"
    return orch, app, artifact


def test_candidate_verify_passes_in_stage_preserves_original_and_cleans(tmp_path, monkeypatch):
    import hashlib

    orch, original, artifact = _candidate_verify_fixture(tmp_path)
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    stage_base = tmp_path / "stages"
    stage_base.mkdir()
    real_mkdtemp = orchestrator.tempfile.mkdtemp
    monkeypatch.setattr(
        orchestrator.tempfile, "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=stage_base))

    ok, output, scope = orch._run_round_verify(artifact, 1)

    assert ok is True and output == ""
    assert scope == "candidate"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert original.read_text(encoding="utf-8") == "broken"
    assert list(stage_base.iterdir()) == []


def test_candidate_verify_failure_is_candidate_scoped(tmp_path):
    orch, original, artifact = _candidate_verify_fixture(tmp_path, "still-broken")

    ok, _, scope = orch._run_round_verify(artifact, 2)

    assert ok is False
    assert scope == "candidate"
    assert original.read_text(encoding="utf-8") == "broken"


def test_verify_without_candidate_keeps_original_tree_behavior(tmp_path, monkeypatch):
    orch, _, _ = _candidate_verify_fixture(tmp_path)
    monkeypatch.setattr(
        orchestrator.shutil, "copytree",
        lambda *args, **kwargs: pytest.fail("후보 없는 런은 스테이징하면 안 됨"))

    ok, _, scope = orch._run_round_verify("ordinary text output", 1)

    assert ok is False
    assert scope == "original_tree"
    assert orch.materialize == {} and orch.changes == {}


def test_candidate_verify_drives_strict_gate_and_calibration_scope(tmp_path, monkeypatch):
    orch, original, artifact = _candidate_verify_fixture(tmp_path)

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        orch._step_i += 1
        if task_kind == "critic":
            text, score = "SCORE: 9\nindependent approval", 9.0
        else:
            text, score = artifact, None
        orch.steps.append(orchestrator.StepLog(
            orch._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orchestrator.knot, "save", lambda *args, **kwargs: None)

    orch.run_producer_reviewer(
        "fix app", "claude-main", "codex-critic", max_rounds=1, pass_score=8.0)

    assert orch.gate["passed"] is True
    assert orch.gate["verify_ok"] is True
    assert original.read_text(encoding="utf-8") == "broken"
    records = [json.loads(line) for line in
               (orch.cfg.paths.runs.parent / "calibration.jsonl").read_text(
                   encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["verify_scope"] == "candidate"
    assert records[0]["gate_pass"] is True


def test_staging_copy_failure_falls_back_and_cleans(tmp_path, monkeypatch):
    orch, original, artifact = _candidate_verify_fixture(tmp_path)
    stage_base = tmp_path / "failed-stages"
    stage_base.mkdir()
    real_mkdtemp = orchestrator.tempfile.mkdtemp
    monkeypatch.setattr(
        orchestrator.tempfile, "mkdtemp",
        lambda prefix: real_mkdtemp(prefix=prefix, dir=stage_base))
    monkeypatch.setattr(
        orchestrator.shutil, "copytree",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("copy denied")))

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        orch._step_i += 1
        text = "SCORE: 9\nreview" if task_kind == "critic" else artifact
        score = 9.0 if task_kind == "critic" else None
        orch.steps.append(orchestrator.StepLog(
            orch._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(orch, "call_worker", fake_call)
    monkeypatch.setattr(orchestrator.knot, "save", lambda *args, **kwargs: None)
    orch.run_producer_reviewer(
        "copy failure", "claude-main", "codex-critic", max_rounds=1)

    assert orch.gate["passed"] is False        # broken 원본 verify로 정상 폴백
    assert orch._calib_rounds[0]["verify_scope"] == "original_tree"
    status = json.loads((orch.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"           # 준비 실패가 런 자체를 죽이지 않음
    assert original.read_text(encoding="utf-8") == "broken"
    assert list(stage_base.iterdir()) == []
    log = (orch.run_dir / "run.log").read_text(encoding="utf-8")
    assert "스테이징 실패: OSError: copy denied — 원본 verify 폴백" in log


def test_stage_file_limit_skips_copy_and_falls_back(tmp_path, monkeypatch):
    orch, _, artifact = _candidate_verify_fixture(tmp_path)
    orch.changes = {"stage_max_files": 1}       # app.txt + check.py = 2
    monkeypatch.setattr(
        orchestrator.shutil, "copytree",
        lambda *args, **kwargs: pytest.fail("상한 초과면 copytree를 호출하면 안 됨"))

    ok, _, scope = orch._run_round_verify(artifact, 1)

    assert ok is False
    assert scope == "original_tree"
    log = (orch.run_dir / "run.log").read_text(encoding="utf-8")
    assert "파일 상한 초과(2>1) — 원본 verify 폴백" in log


def test_unsafe_file_block_prevents_partial_candidate_label(tmp_path):
    orch, original, artifact = _candidate_verify_fixture(tmp_path)
    artifact += "\n```file:../escape.txt\nevil\n```"

    ok, _, scope = orch._run_round_verify(artifact, 1)

    assert ok is False                         # valid 일부만 적용해 통과시키지 않음
    assert scope == "original_tree"
    assert original.read_text(encoding="utf-8") == "broken"
    assert not (tmp_path / "escape.txt").exists()


def test_calibration_logging_failure_does_not_break_run(mock_root, monkeypatch):
    o = Orchestrator(Config.load(mock_root), auto=True)
    o._calib_rounds.append({"score": 1.0, "round": 1})
    monkeypatch.setattr(
        calibration, "make_record", lambda **kwargs: (_ for _ in ()).throw(TypeError("boom")))

    o._log_calibration()

    assert "[calib] 기록 실패: TypeError: boom" in (o.run_dir / "run.log").read_text(encoding="utf-8")


# ----------------------------------------------- GUI 작업(task) CRUD
def test_task_crud_save_load_delete(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "슬러그 함수 구현",
            "producer": "claude-main", "reviewer": "codex-critic"}
    r = gs._save_task(cfg, "슬러그 함수", spec)                     # 한글 이름 → 유니코드 slug
    assert r["ok"] and r["name"] == "task-슬러그-함수.json"
    assert r["name"] in gs._list_tasks(cfg)                        # 목록에 등장
    loaded = gs._load_task(cfg, r["name"])
    assert loaded["ok"] and loaded["spec"]["task"] == "슬러그 함수 구현"
    assert loaded["spec"]["label"] == "슬러그 함수"                 # 라벨=이름(작업별 콘솔 연동)
    assert gs._delete_task(cfg, r["name"])["ok"]
    assert r["name"] not in gs._list_tasks(cfg)                    # 삭제됨


def test_validate_task_spec_allows_empty_goal_only_for_draft(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "", "agents": {}}

    assert gs._validate_task_spec(spec, cfg, allow_draft=True) == ""
    assert "목표" in gs._validate_task_spec(spec, cfg)              # 실행 검증은 계속 엄격함


def test_validate_task_spec_rejects_gate_mode_errors_early(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    base = {"pattern": "producer-reviewer", "task": "t"}

    assert "score_gate_mode" in gs._validate_task_spec(
        {**base, "score_gate_mode": "auto", "verify_cmd": "pytest -q"}, cfg)
    assert "verify_cmd" in gs._validate_task_spec(
        {**base, "score_gate_mode": "advisory"}, cfg)
    assert "verify_cmd" in gs._validate_task_spec(
        {**base, "score_gate_mode": "advisory", "verify_cmd": "   "}, cfg)
    assert gs._validate_task_spec(
        {**base, "score_gate_mode": "advisory", "verify_cmd": "pytest -q"}, cfg) == ""


@pytest.mark.parametrize("mode,verify_cmd", [("advisory", None), ("auto", "verify")])
def test_task_gate_config_error_aborts_before_worker(
        mock_root, monkeypatch, mode, verify_cmd):
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "t",
            "producer": "claude-main", "reviewer": "codex-critic",
            "score_gate_mode": mode}
    if verify_cmd is not None:
        spec["verify_cmd"] = verify_cmd
    tf = mock_root / "task-gate-error.json"
    tf.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(
        Orchestrator, "call_worker",
        lambda *a, **k: pytest.fail("설정 오류에서 워커를 호출하면 안 됨"))

    result = run_task_file(cfg, tf, auto=True)

    assert result.startswith("aborted:")
    run_dir = max(cfg.paths.runs.iterdir(), key=lambda p: p.name)
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "aborted" and status["cause"] == "config_error"


def test_gui_score_gate_mode_is_wired_for_build_open_and_reset():
    html = (Path(__file__).parents[1] / "gui" / "index.html").read_text(encoding="utf-8")

    assert '<select id="c-gatemode">' in html
    assert 'score_gate_mode:gateMode' in html
    assert 'set("c-gatemode","strict")' in html
    assert 's.score_gate_mode||"strict"' in html
    assert '<option value="auto">' not in html


def test_saved_task_with_invalid_gate_mode_is_rejected_before_enqueue(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    name = "task-invalid-gate.json"
    (mock_root / name).write_text(json.dumps({
        "pattern": "producer-reviewer", "task": "t",
        "score_gate_mode": "auto", "verify_cmd": "pytest -q",
    }), encoding="utf-8")

    result = gs._enqueue_saved_task(cfg, name, 1)

    assert "score_gate_mode" in result["error"]


def test_draft_task_save_list_and_load_roundtrip(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    spec = {"label": "새 초안", "pattern": "producer-reviewer", "task": "", "agents": {}}

    saved = gs._save_task(cfg, "새 초안", spec)
    loaded = gs._load_task(cfg, saved["name"])

    assert saved["ok"] and loaded["ok"]
    assert {"name": saved["name"], "label": "새 초안"} in gs._saved_tasks(cfg)
    assert loaded["spec"] == spec


def test_draft_registered_run_is_rejected_before_enqueue(mock_root, monkeypatch):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    saved = gs._save_task(cfg, "실행 전 초안",
                          {"pattern": "producer-reviewer", "task": "", "agents": {}})
    enqueued = []
    monkeypatch.setattr(gs, "_enqueue", lambda *args: enqueued.append(args) or {"ok": True})

    result = gs._enqueue_saved_task(cfg, saved["name"], 1)

    assert result == {"error": "목표가 비었다 — 작업을 열어 목표를 입력하라"}
    assert enqueued == []                                                # 런/큐 시작 전 차단


def test_rename_task_preserves_spec_and_updates_label(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "계산기 구현",
            "producer": "claude-main", "reviewer": "codex-critic",
            "agents": {"claude-main": {"backend": "codex", "effort": "high"}},
            "workdir": "C:/work/project", "verify_cmd": "python -m pytest -q"}
    saved = gs._save_task(cfg, "기존 이름", spec)

    renamed = gs._rename_task(cfg, saved["name"], "바뀐 이름")
    loaded = gs._load_task(cfg, renamed["name"])

    assert renamed == {"ok": True, "name": "task-바뀐-이름.json", "label": "바뀐 이름"}
    assert not (mock_root / saved["name"]).exists()
    assert (mock_root / renamed["name"]).exists()
    assert loaded["spec"] == {**spec, "label": "바뀐 이름"}       # 목표·패턴·배치·workdir 보존


def test_rename_task_rejects_duplicate_and_preserves_original(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    first = gs._save_task(cfg, "첫 작업", {"pattern": "producer-reviewer", "task": "첫 목표"})
    second = gs._save_task(cfg, "둘째 작업", {"pattern": "pipeline", "task": "둘째 목표"})
    before = (mock_root / first["name"]).read_text(encoding="utf-8")

    result = gs._rename_task(cfg, first["name"], "둘째 작업")

    assert "error" in result and "이미" in result["error"]
    assert (mock_root / first["name"]).read_text(encoding="utf-8") == before
    assert (mock_root / second["name"]).exists()


def test_rename_task_rejects_missing_source(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)

    assert gs._rename_task(cfg, "task-없는-작업.json", "새 이름") == {"error": "없는 작업"}


def test_saved_tasks_expose_label_for_console_unification(mock_root):
    # 저장된 작업이 label과 함께 노출돼 '작업별 보기'(런 라벨)와 통합된다.
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    gs._save_task(cfg, "계산기 앱", {"pattern": "producer-reviewer", "task": "계산기",
                                    "producer": "claude-main", "reviewer": "codex-critic"})
    saved = gs._saved_tasks(cfg)
    assert any(t["name"] == "task-계산기-앱.json" and t["label"] == "계산기 앱" for t in saved)


def test_task_crud_rejects_path_traversal_and_bad_spec(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    assert gs._task_path(cfg, "../evil.json") is None             # 경로순회 차단
    assert gs._task_path(cfg, "task-a/b.json") is None            # 슬래시 차단
    assert gs._task_path(cfg, "notes.json") is None               # task- 접두 아님
    assert "목표" in gs._validate_task_spec({"pattern": "producer-reviewer", "task": ""})        # 빈 task
    assert "error" in gs._save_task(cfg, "잘못패턴", {"pattern": "bad", "task": "x"})              # 잘못된 pattern
    assert "error" in gs._save_task(cfg, "!!!", {"pattern": "producer-reviewer", "task": "x"})     # slug 빈값
    assert "error" in gs._load_task(cfg, "task-없는것.json")       # 없는 작업


# ----------------------------------------------------------------- 가드 자동 정지
def test_guard_stops_when_ledger_budget_exceeded(mock_root):
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False   # 원장만(결정적, 라이브 probe 배제)
    cfg.yok3x["budgets"]["gemini"] = {"daily_calls": 1}
    usage.record(cfg, "gemini", "build", BackendResult(backend="gemini", ok=True))
    allowed, verdict = usage.guard_allows(cfg, "gemini")
    assert allowed is False and verdict.level == "stop"


def test_guard_allows_when_under_budget(mock_root):
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False
    cfg.yok3x["budgets"]["gemini"] = {"daily_calls": 100}
    allowed, verdict = usage.guard_allows(cfg, "gemini")
    assert allowed is True and verdict.level == "ok"


# ------------------------------------------------------------------- BOM 방어 로드
def test_config_load_tolerates_utf8_bom(tmp_path):
    scaffold(tmp_path, use_mock=True)
    payload = json.dumps({"context_max_chars": 1234}, ensure_ascii=False)
    (tmp_path / "yok3x.json").write_text("﻿" + payload, encoding="utf-8")  # BOM 부착
    cfg = Config.load(tmp_path)
    assert cfg.yok3x["context_max_chars"] == 1234


# ------------------------------------ CLI 모델 목록 동적 조회(하드코딩 아님)
def test_list_models_dynamic(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    limits._MODELS_CACHE.clear()
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-5.6-sol", "visibility": "list"},
        {"slug": "hidden-model", "visibility": "hide"}]}), encoding="utf-8")
    monkeypatch.setattr(limits.Path, "home", classmethod(lambda cls: home))
    assert limits.list_models(cfg, "codex") == ["gpt-5.6-sol"]      # 캐시 slug, hide 제외
    limits._MODELS_CACHE.clear()
    monkeypatch.setattr(limits, "_gemini_bundle_dir", lambda: None)  # 번들도 없는 최초 경로
    assert limits.list_models(cfg, "gemini") == []                  # 키·번들 없음 → 빈 목록(명시적 폴백)

    # gemini: 키가 있으면 Google /v1beta/models 실제 조회 → generateContent 지원 모델만
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    payload = json.dumps({"models": [
        {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},  # 제외
    ]}).encode()
    monkeypatch.setattr(limits.urllib.request, "urlopen",
                        lambda url, timeout=0: _Resp(payload))
    limits._MODELS_CACHE.clear()
    assert limits.list_models(cfg, "gemini") == ["gemini-3-pro", "gemini-2.5-flash"]


def test_gemini_bundle_registry_parse(tmp_path, monkeypatch):
    # 키가 없을 때 gemini CLI 번들의 GEMINI_MODELS Set(변수참조)을 해석해 슬러그를 뽑는다.
    bundle = tmp_path / "bundle"; bundle.mkdir()
    (bundle / "chunk-x.js").write_text(
        'var PREVIEW_GEMINI_MODEL = "gemini-3-pro-preview";\n'
        'var DEFAULT_GEMINI_MODEL = "gemini-2.5-pro";\n'
        'var DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.5-flash";\n'
        'var GEMMA_MODEL = "gemma-4-31b-it";\n'
        'var NOISE = "gemini-9001-super-duper";\n'   # Set에 없으면 제외돼야
        'GEMINI_MODELS = /* @__PURE__ */ new Set([\n'
        '  PREVIEW_GEMINI_MODEL, DEFAULT_GEMINI_MODEL, DEFAULT_GEMINI_FLASH_MODEL, GEMMA_MODEL\n'
        ']);\n', encoding="utf-8")
    monkeypatch.setattr(limits, "_gemini_bundle_dir", lambda: bundle)
    got = limits._gemini_bundle_models()
    assert got == ["gemini-3-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemma-4-31b-it"]
    assert "gemini-9001-super-duper" not in got   # Set 멤버 아님 → 노이즈 제외


def test_config_version_matches_version_module():
    # GUI가 보여주는 버전은 _version.py 단일 출처와 일치해야 한다(config 하드코딩 오염 금지).
    from yok3x._version import __version__ as v
    from yok3x.config import DEFAULT_YOK3X
    assert DEFAULT_YOK3X["version"] == v


def test_gemini_api_key_resolution(tmp_path, monkeypatch):
    for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    assert limits._gemini_api_key({}) == ""                          # 아무데도 없음
    assert limits._gemini_api_key({"api_key": " k1 "}) == "k1"       # 직접(공백 트림)
    f = tmp_path / "key"; f.write_text("k2\n", encoding="utf-8")
    assert limits._gemini_api_key({"api_key_path": str(f)}) == "k2"  # 파일
    monkeypatch.setenv("GOOGLE_API_KEY", "k3")
    assert limits._gemini_api_key({}) == "k3"                        # env(기본 목록)


# ------------------------------------ 미보정 추정 false-stop 방지
def test_uncalibrated_estimate_does_not_hard_stop(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    est = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                              windows=[limits.Window("5h", 788.0)], detail="est")
    monkeypatch.setattr(limits, "probe", lambda c, b, use_cache=True: est)
    assert usage.check_backend(cfg, "claude").level == "warn"        # 추정 788% → 정지 유보
    live = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("5h", 788.0)], detail="live")
    monkeypatch.setattr(limits, "probe", lambda c, b, use_cache=True: live)
    assert usage.check_backend(cfg, "claude").level == "stop"        # 실측 788% → 정지


# ------------------------------------ codex 한도 창 라벨(길이 기준, 위치 무관)
def test_window_name_from_duration():
    assert limits._window_name(300) == "5h"
    assert limits._window_name(10080) == "7d"
    assert limits._window_name(1440) == "1d"
    assert limits._window_name(120) == "2h"
    assert limits._window_name(None) == "?"


def test_codex_appserver_labels_windows_by_duration(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    # primary에 7일(10080분), secondary에 5h(300분)가 와도 '길이'로 정확히 라벨해야 한다.
    monkeypatch.setattr(limits, "_appserver_rate_limits", lambda exe, args, to: {
        "primary": {"usedPercent": 4, "windowDurationMins": 10080, "resetsAt": 1e9},
        "secondary": {"usedPercent": 2, "windowDurationMins": 300, "resetsAt": 1e9},
        "planType": "plus"})
    r = limits._probe_codex_appserver("codex", cfg.yok3x["limits"]["codex"])
    names = {w.name: w.used_percent for w in r.windows}
    assert names.get("7d") == 4.0 and names.get("5h") == 2.0      # 위치 아닌 길이로 라벨


# ------------------------------------ codex JSONL 파서(신형 스키마 호환)
def test_parse_codex_new_item_completed_schema():
    # codex 0.144: agent 메시지가 item.completed 이벤트의 item.type=="agent_message".
    out = "\n".join([
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message",'
        '"text":"SCORE: 9\\n- 엣지케이스 처리 양호"}}',
        '{"type":"turn.completed","usage":{"input_tokens":13281,"output_tokens":29}}',
    ])
    res = backends._parse_codex(out)
    assert res.ok and res.text.startswith("SCORE: 9")     # 어시스턴트 텍스트 추출
    assert res.total_tokens == 13281 + 29                 # turn.completed 사용량 집계


# ------------------------------------ claude 라이브 실측(OAuth usage 엔드포인트)
class _Resp:
    def __init__(self, payload, status=200): self._p = payload; self.status = status
    def read(self): return self._p
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_local_openai_http_adapter(monkeypatch):
    # 로컬 OpenAI 호환 서버 호출: choices[0].message.content 파싱, <think> 제거, cost=0.
    from yok3x import backends
    payload = json.dumps({"choices": [{"message": {"content":
        "<think>reasoning...</think>\n`lambda s: s==s[::-1]`"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode()
    monkeypatch.setattr(backends.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(payload))
    res = backends.run_backend("local", {"type": "openai_http",
                               "base_url": "http://localhost:8000/v1"}, "prompt")
    assert res.ok and res.text == "`lambda s: s==s[::-1]`"   # think 제거됨
    assert res.cost_usd == 0.0 and res.total_tokens == 20     # 로컬=무료


def test_effort_passthrough_argv(monkeypatch):
    # 워커 effort가 backend별 effort_arg로 argv에 붙는지(claude --effort, codex -c ...). 미지정 시 미부착.
    from yok3x import backends
    cap = {}
    class _P:
        stdout = '{"result":"ok","is_error":false}'; stderr = ""; returncode = 0
    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: (cap.__setitem__("c", cmd), _P())[1])
    monkeypatch.setattr(backends.shutil, "which", lambda x: x)
    cl = {"type": "cli", "command": ["claude", "-p"], "effort_arg": ["--effort", "{effort}"], "parser": "raw"}
    backends.run_backend("claude", cl, "hi", effort="high")
    assert cap["c"] == ["claude", "-p", "--effort", "high"]
    cx = {"type": "cli", "command": ["codex", "exec"],
          "effort_arg": ["-c", "model_reasoning_effort={effort}"], "parser": "raw"}
    backends.run_backend("codex", cx, "hi", effort="medium")
    assert cap["c"] == ["codex", "exec", "-c", "model_reasoning_effort=medium"]
    backends.run_backend("claude", cl, "hi")             # effort 미지정 → 미부착
    assert cap["c"] == ["claude", "-p"]


def test_daily_pace_accumulate_delta_and_levels(tmp_path):
    # 하루 페이싱: 첫 관측 이후 7d%의 양의 증분 누적 = 오늘 소비. soft/cap 경계와 자정 리셋.
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, pct_of_weekly=0.2, soft_frac=0.8, mode="warn")
    assert usage.daily_pace_status(cfg, "claude", 30.0, today="2026-07-14")["used"] == 0.0     # 첫 관측
    assert usage.daily_pace_status(cfg, "claude", 40.0, today="2026-07-14")["level"] == "ok"   # +10
    assert usage.daily_pace_status(cfg, "claude", 47.0, today="2026-07-14")["level"] == "warn"  # 누적17≥soft16
    over = usage.daily_pace_status(cfg, "claude", 52.0, today="2026-07-14")                     # 누적22≥cap20
    assert over["used"] >= over["cap"] and over["level"] == "warn"        # mode=warn → 정지 아님
    nxt = usage.daily_pace_status(cfg, "claude", 52.0, today="2026-07-15")  # 다음 날 → 리셋
    assert nxt["used"] == 0.0 and nxt["level"] == "ok"


def test_daily_pace_sticky_block_survives_rolloff_and_probe_fail(tmp_path, monkeypatch):
    # codex 리뷰 반영: pause는 cap 도달 후 값이 낮아져도(롤오프) 자동 재개 안 함(sticky). 승인/자정만 해제.
    import datetime as _dt
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, pct_of_weekly=0.1, soft_frac=0.8, mode="pause")
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    usage.daily_pace_status(cfg, "codex", 20.0, today=today)              # 첫 관측
    s = usage.daily_pace_status(cfg, "codex", 32.0, today=today)          # +12 ≥ cap10 → blocked
    assert s["blocked"] and s["level"] == "stop"
    s2 = usage.daily_pace_status(cfg, "codex", 25.0, today=today)         # 롤오프 하락 → 그래도 정지 유지
    assert s2["blocked"] and s2["level"] == "stop"
    assert usage.pace_block_active(cfg, "codex", today=today) is True     # probe 실패 경로에서도 정지
    usage.pace_approve(cfg, "codex", today=today)                         # 승인
    assert usage.pace_block_active(cfg, "codex", today=today) is False
    assert usage.daily_pace_status(cfg, "codex", 40.0, today=today)["level"] == "ok"


def test_daily_pace_only_on_real_and_ledger_block(tmp_path, monkeypatch):
    # 추정치(real=False)로는 페이싱 정지 안 함. probe 실패해도 저장된 sticky block이면 stop 유지.
    import datetime as _dt
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, pct_of_weekly=0.1, mode="pause")
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    # 추정 reading(real=False)에 7d 높아도 페이싱 미적용 → daily_pace로 stop 아님
    W = limits.Window
    est = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                              windows=[W("5h", 5.0), W("7d", 50.0)], detail="추정")
    monkeypatch.setattr(limits, "probe", lambda c, b, use_cache=True: est)
    usage._save_pace(cfg, {"claude": {"date": today, "start_pct": 40.0, "last_pct": 40.0,
                                      "used_today": 0.0, "blocked": False}})
    assert usage.check_backend(cfg, "claude").metric != "daily_pace"      # 추정치 → 페이싱 미적용
    # probe 실패 + 저장된 blocked → stop 유지(codex 리뷰 #4)
    fail = limits.LimitReading("codex", "codex_appserver", ok=False, real=False, error="fail")
    monkeypatch.setattr(limits, "probe", lambda c, b, use_cache=True: fail)
    usage._save_pace(cfg, {"codex": {"date": today, "start_pct": 0.0, "last_pct": 15.0,
                                     "used_today": 15.0, "blocked": True}})
    v = usage.check_backend(cfg, "codex")
    assert v.level == "stop" and v.metric == "daily_pace"


def test_p3_offline_failover(monkeypatch, tmp_path):
    # 클라우드 전부 stop이면 로컬 서버가 떠 있을 때만 local로 강등(P3).
    cfg = Config.load(tmp_path)
    stop = usage.GuardVerdict("claude", 1.5, "x", "stop", "한도")
    monkeypatch.setattr(usage, "check_backend", lambda c, b: stop)   # 모든 백엔드 stop
    monkeypatch.setattr(usage, "offline_reachable", lambda c, b="local": True)
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) == "local"   # → 강등
    monkeypatch.setattr(usage, "offline_reachable", lambda c, b="local": False)
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) is None      # 서버 없으면 정지
    monkeypatch.setattr(usage, "offline_reachable", lambda c, b="local": True)
    cfg.yok3x["guard"]["degrade"]["offline_enabled"] = False
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) is None      # off면 강등 안 함


def test_claude_oauth_parses_live_5h_7d(monkeypatch, tmp_path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "tok", "expiresAt": 99999999999000}}), encoding="utf-8")
    payload = json.dumps({
        "five_hour": {"utilization": 17.0, "resets_at": "2026-07-10T11:39:59+00:00"},
        "seven_day": {"utilization": 3.0, "resets_at": "2026-07-12T08:59:59+00:00"},
    }).encode()
    monkeypatch.setattr(limits.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(payload))
    conf = {"type": "claude_oauth", "credentials_path": str(creds), "min_interval_sec": 0}
    r = limits._probe_claude_oauth("claude", conf)
    assert r.ok and r.real and r.source == "claude_oauth"      # 실측(추정 아님)
    got = {w.name: (w.used_percent, w.resets_at) for w in r.windows}
    assert got["5h"][0] == 17.0 and got["7d"][0] == 3.0
    assert got["5h"][1] and got["7d"][1]                        # 리셋 시각 파싱됨


def test_claude_oauth_surfaces_per_model_and_credits(monkeypatch, tmp_path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "t", "expiresAt": 99999999999000}}), encoding="utf-8")
    payload = json.dumps({
        "five_hour": {"utilization": 20.0, "resets_at": "2026-07-10T11:39:59+00:00"},
        "seven_day": {"utilization": 4.0, "resets_at": "2026-07-12T08:59:59+00:00"},
        "seven_day_opus": {"utilization": 31.0, "resets_at": "2026-07-12T08:59:59+00:00"},
        "seven_day_sonnet": None,   # null이면 생략돼야 함
        "extra_usage": {"is_enabled": True, "used_credits": 12.5,
                        "monthly_limit": 50, "utilization": 25.0, "currency": "USD"},
    }).encode()
    monkeypatch.setattr(limits.urllib.request, "urlopen",
                        lambda req, timeout=0: _Resp(payload))
    conf = {"type": "claude_oauth", "credentials_path": str(creds), "min_interval_sec": 0}
    r = limits._probe_claude_oauth("claude", conf)
    names = {w.name for w in r.windows}
    assert "7d·opus" in names            # 모델별(값 있을 때) 표시
    assert "7d·sonnet" not in names      # null이면 생략
    assert "크레딧 12.5/50" in r.detail   # 추가크레딧(활성 시) 표시
    # 크레딧은 guard 창이 아니다 — ratio는 창 최대(31%)여야지 크레딧에 오염되면 안 됨
    assert abs(r.ratio() - 0.31) < 1e-6


def test_claude_oauth_falls_back_when_no_credentials(tmp_path):
    # 토큰 없고 추정 캡도 없으면 ok=False로 내려가 원장 폴백에 맡긴다(명시적 열화).
    limits._OAUTH_LIVE_CACHE.clear()   # 이전 실측 stale 캐시 없는(최초) 경로를 검증
    conf = {"type": "claude_oauth", "min_interval_sec": 0, "max_stale_sec": 900,
            "credentials_path": str(tmp_path / "nope.json")}
    r = limits._probe_claude_oauth("claude", conf)
    assert not r.ok and "credentials" in r.error


def test_live_failure_keeps_last_reading_as_stale(monkeypatch, tmp_path):
    # 실측이 429로 실패하면 원장으로 깜빡이지 말고 마지막 실측을 '⚠N분 전 실측'으로 유지해야 한다.
    conf = {"type": "claude_oauth", "min_interval_sec": 0, "max_stale_sec": 900,
            "credentials_path": str(tmp_path / "nope.json")}
    good = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("5h", 14.0)], detail="5h 14% (live)")
    limits._OAUTH_LIVE_CACHE["claude"] = (1000.0, good)   # 과거 성공 실측 캐시
    monkeypatch.setattr(limits.time, "time", lambda: 1000.0 + 120)   # 2분 뒤, live는 실패
    r = limits._probe_claude_oauth("claude", conf)
    assert r.ok and r.real                       # 실측 배지 유지(원장으로 안 떨어짐)
    assert r.ratio() == 0.14                      # 마지막 실측 수치 그대로
    assert "전 실측" in r.detail                   # stale 표시
    # max_stale 지나면 더는 유지하지 않는다 → 폴백(ok=False)
    monkeypatch.setattr(limits.time, "time", lambda: 1000.0 + 2000)
    r2 = limits._probe_claude_oauth("claude", conf)
    assert not r2.ok
    limits._OAUTH_LIVE_CACHE.clear()


def test_implausible_estimate_is_dropped_for_ledger(monkeypatch, tmp_path):
    # live 실패 + 미보정 추정이 비현실적(>200%)이면 그 값을 표시하지 않고 ok=False로 내려
    # 원장 폴백에 맡긴다("1003%" 오표시 방지). 현실적(<200%)이면 추정을 그대로 쓴다.
    conf = {"type": "claude_oauth", "min_interval_sec": 0,
            "credentials_path": str(tmp_path / "nope.json")}   # live 실패 강제
    W = limits.Window
    hi = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                             windows=[W("5h", 1003.6), W("7d", 778.1)])
    monkeypatch.setattr(limits, "_probe_claude_transcripts", lambda b, c: hi)
    r = limits._probe_claude_oauth("claude", conf)
    assert not r.ok and "무시" in r.error          # 비현실적 추정 → 버리고 원장으로

    lo = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                             windows=[W("5h", 62.0), W("7d", 20.0)])
    monkeypatch.setattr(limits, "_probe_claude_transcripts", lambda b, c: lo)
    r2 = limits._probe_claude_oauth("claude", conf)
    assert r2.ok and abs(r2.ratio() - 0.62) < 1e-6  # 현실적 추정은 유지


# -------------------------------------- claude transcript 자동 캘리브레이션 + 토큰 노출
def test_autocalibrate_claude_saves_cap_from_live_percent(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 1_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                                  windows=[limits.Window("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] == 10_000_000 and got["7d"] is None
    assert cfg.yok3x["limits"]["claude"]["limit_5h_tokens"] == 10_000_000
    saved = json.loads(cfg.paths.yok3x_json.read_text(encoding="utf-8"))
    assert saved["limits"]["claude"]["limit_5h_tokens"] == 10_000_000


@pytest.mark.parametrize("pct,toks,reason", [
    (0.5, 1_000_000, "live_pct"),
    (10.0, 0, "tokens<=0"),
])
def test_autocalibrate_claude_rejects_small_percent_or_zero_tokens(
        tmp_path, monkeypatch, pct, toks, reason):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 12_345_678
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: toks)

    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [limits.Window("5h", pct)]))

    assert got["5h"] is None and reason in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 12_345_678
    assert not cfg.paths.yok3x_json.exists()


def test_autocalibrate_claude_rate_limits_writes(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 1_000_000)
    first = limits.LimitReading("claude", "claude_oauth", True, True,
                                [limits.Window("5h", 10.0)])
    second = limits.LimitReading("claude", "claude_oauth", True, True,
                                 [limits.Window("7d", 10.0)])
    limits.autocalibrate_claude(cfg, conf, first)

    got = limits.autocalibrate_claude(cfg, conf, second)

    assert got["skipped"] == "rate-limit"
    assert conf["limit_7d_tokens"] == 0


def test_autocalibrate_claude_rejects_implausible_cap(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    limits._CLAUDE_CALIBRATION_STATE.clear()
    # 10%에서 200억 tok이면 2000억 cap = 기존의 20,000배 → 오염된 표본으로 본다.
    # 상한은 max_calib_multiple(기본 1000배). 실측상 7d는 cache read 누적으로 정상적으로도
    # ~153배가 나오므로(5h는 ~1배) 100배로 막으면 정상 보정이 거부된다 — 그래서 1000배다.
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 20_000_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [limits.Window("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "비현실" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000


def test_autocalibrate_claude_allows_cache_read_inflation(tmp_path, monkeypatch):
    """실측 근거: 7d는 cache read 누적으로 plan 대비 ~153배 캡이 정상이다(5h는 ~1배).
    100배로 막으면 정상 보정이 거부되므로, 설정 상한(max_calib_multiple=1000) 안이면 통과해야 한다."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_7d_tokens"] = 500_000_000          # plan 프리셋 수준
    limits._CLAUDE_CALIBRATION_STATE.clear()
    # 5%에서 38.3억 tok → cap 766억 = 기존의 약 153배(실측에서 관측된 실제 배율)
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 3_830_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [limits.Window("7d", 5.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["7d"] == 76_600_000_000, got
    assert conf["limit_7d_tokens"] == 76_600_000_000


def test_autocalibrate_claude_multiple_bounds_come_from_config(tmp_path, monkeypatch):
    """가드 경계는 하드코딩이 아니라 설정이어야 한다(RULE §5.5)."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    conf["max_calib_multiple"] = 2.0               # 상한을 좁히면 거부돼야
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 10_000_000)  # 10%→1억=10배
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [limits.Window("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "비현실" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000


def test_autocalibrate_claude_skips_negligible_change(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    limits._CLAUDE_CALIBRATION_STATE.clear()
    # 역산 cap=10.5M: 기존 대비 정확히 +5%라 파일 churn 없이 유지한다.
    monkeypatch.setattr(limits, "claude_rolling_tokens", lambda c, w: 1_050_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [limits.Window("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "변화<=5%" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000
    assert not cfg.paths.yok3x_json.exists()


def test_autocalibrate_claude_ignores_per_model_window(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens",
                        lambda c, w: pytest.fail("per-model 창은 transcript를 읽으면 안 됨"))
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [limits.Window("7d·Fable", 22.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and got["7d"] is None
    assert conf["limit_5h_tokens"] == 0 and conf["limit_7d_tokens"] == 0


def test_autocalibrate_claude_off_preserves_existing_behavior(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["autocalibrate"] = False
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_rolling_tokens",
                        lambda c, w: pytest.fail("off이면 transcript를 읽으면 안 됨"))

    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [limits.Window("5h", 10.0)]))

    assert "비활성" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 0 and not cfg.paths.yok3x_json.exists()


def test_claude_transcript_tokens_are_exposed_in_gui_state(tmp_path, monkeypatch):
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf.update({"projects_dir": str(tmp_path), "limit_5h_tokens": 10_000,
                 "limit_7d_tokens": 20_000})
    monkeypatch.setattr(limits, "_rolling_claude_tokens",
                        lambda root, now, secs: 2_000 if secs == 5 * 3600 else 3_000)
    reading = limits._probe_claude_transcripts("claude", conf)
    by_name = {w.name: w for w in reading.windows}
    assert (by_name["5h"].used_tokens, by_name["5h"].limit_tokens) == (2_000, 10_000)
    assert (by_name["7d"].used_tokens, by_name["7d"].limit_tokens) == (3_000, 20_000)

    verdict = usage.GuardVerdict("claude", reading.ratio(), "x", "ok", reading.detail,
                                 source=reading.source, real=False, reading=reading)
    monkeypatch.setattr(gs.usage, "today_totals", lambda c: {})
    monkeypatch.setattr(gs.usage, "check_backend", lambda c, b: verdict)
    monkeypatch.setattr(gs.usage, "coach_messages", lambda c: [])
    monkeypatch.setattr(gs, "_routing_preview", lambda c: {})
    monkeypatch.setattr(gs, "_profile_routes", lambda c: {})
    monkeypatch.setattr(gs.limits, "list_models", lambda c, b: [])
    monkeypatch.setattr(gs.limits, "claude_token_status", lambda c: {})
    state = gs.build_state(cfg)
    win = state["tools"][0]["windows"][0]
    assert win["used_tokens"] == 2_000 and win["limit_tokens"] == 10_000
    # 배포 HTML은 저장소 gui를 쓰므로 사용자 요청 문구와 None 가드를 정적으로도 잠근다.
    html = (Path(__file__).parents[1] / "gui" / "index.html").read_text(encoding="utf-8")
    assert "사용 ${fmtTok(w.used_tokens)} / 남은" in html and "w.used_tokens!=null" in html


def test_live_calibration_failure_does_not_hide_live_reading(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["min_interval_sec"] = 0
    live = limits.LimitReading("claude", "claude_oauth", True, True,
                               [limits.Window("5h", 10.0)], detail="5h 10% (live)")
    limits._OAUTH_LIVE_CACHE.clear()
    monkeypatch.setattr(limits, "_fetch_claude_oauth_usage", lambda b, c: live)
    monkeypatch.setattr(limits, "autocalibrate_claude",
                        lambda c, co, r: (_ for _ in ()).throw(OSError("disk full")))

    got = limits._probe_claude_oauth("claude", conf, cfg)

    assert got is live and got.ok and got.real


def test_claude_autocalibration_defaults_are_configurable():
    conf = DEFAULT_YOK3X["limits"]["claude"]
    assert conf["autocalibrate"] is True
    assert conf["min_calib_pct"] == 1.0
    assert conf["min_calib_interval_sec"] == 600


# -------------------------------------- 멀티라인 프롬프트 argv 잘림 방어(BUG-18/BUG-10 재발)
def test_multiline_prompt_uses_stdin_even_with_stale_prompt_arg(monkeypatch):
    # 스테일 backends.json이 옛 {prompt}(argv) 형식이어도, 멀티라인이면 stdin으로 넘겨 .cmd 심 잘림 차단.
    from yok3x import backends
    cap = {}
    class _P:
        stdout = "ok"; stderr = ""; returncode = 0
    monkeypatch.setattr(backends.subprocess, "run",
                        lambda cmd, **kw: (cap.update(cmd=cmd, inp=kw.get("input")), _P())[1])
    monkeypatch.setattr(backends.shutil, "which", lambda x: x)
    spec = {"type": "cli", "command": ["tool", "exec", "{prompt}"], "parser": "raw"}
    backends.run_backend("t", spec, "첫 줄\n둘째 줄")                 # 멀티라인
    assert "{prompt}" not in str(cap["cmd"])                        # argv에 {prompt} 없음
    assert "둘째 줄" not in str(cap["cmd"])                          # 프롬프트가 argv에 안 들어감
    assert cap["inp"] == "첫 줄\n둘째 줄"                            # stdin으로 전체 전달


# -------------------------------------- claude 토큰 자체 갱신(near-expiry)
def _write_creds(path, access="old-at", refresh="rt-1", exp_ms=None):
    import time as _t
    if exp_ms is None:
        exp_ms = int((_t.time() + 60) * 1000)   # 60초 뒤 만료(=임박)
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": access, "refreshToken": refresh, "expiresAt": exp_ms}}), encoding="utf-8")


def test_token_refresh_near_expiry_updates_creds(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    _write_creds(creds)                          # 만료 임박
    limits._REFRESH_STATE.clear()
    payload = json.dumps({"access_token": "new-at", "refresh_token": "rt-2",
                          "expires_in": 3600}).encode()
    monkeypatch.setattr(limits.urllib.request, "urlopen", lambda req, timeout=0: _Resp(payload))
    conf = {"credentials_path": str(creds), "auto_refresh": True, "refresh_margin_sec": 300,
            "token_url": "https://x/token", "client_id": "cid"}
    tok, err = limits._claude_oauth_token(conf)
    assert tok == "new-at" and not err                       # 갱신된 토큰 반환
    saved = json.loads(creds.read_text(encoding="utf-8"))["claudeAiOauth"]
    assert saved["accessToken"] == "new-at" and saved["refreshToken"] == "rt-2"  # 회전 저장(원자적)


def test_token_refresh_4xx_circuit_breaks(tmp_path, monkeypatch):
    import urllib.error
    creds = tmp_path / ".credentials.json"; _write_creds(creds)
    limits._REFRESH_STATE.clear()
    def boom(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "invalid_grant", {}, None)
    monkeypatch.setattr(limits.urllib.request, "urlopen", boom)
    conf = {"credentials_path": str(creds), "auto_refresh": True, "refresh_margin_sec": 300,
            "token_url": "https://x/token", "client_id": "cid"}
    # 4xx → 갱신 실패, 기존(만료 임박이나 아직 유효) 토큰으로 폴백. 회로차단됨.
    limits._claude_oauth_token(conf)
    assert limits._REFRESH_STATE[str(creds)]["disabled"]     # 회로차단 기록
    assert json.loads(creds.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == "old-at"  # 안 씀


def test_token_refresh_disabled_when_off_or_no_config(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"; _write_creds(creds)
    limits._REFRESH_STATE.clear()
    called = {"n": 0}
    monkeypatch.setattr(limits.urllib.request, "urlopen",
                        lambda req, timeout=0: called.__setitem__("n", called["n"] + 1))
    limits._claude_oauth_token({"credentials_path": str(creds), "auto_refresh": False})  # off
    assert called["n"] == 0                                   # 갱신 시도 안 함


# -------------------------------------- CLI 백엔드 stdin 데드락 방지(회귀 잠금)
def test_cli_backend_closes_stdin_and_substitutes_prompt(monkeypatch):
    # headless 실행 중 CLI가 대화형 입력을 기다려 데드락하지 않도록 stdin=DEVNULL,
    # 프롬프트는 argv({prompt})로 치환, Windows에서 UTF-8 디코딩이 되어야 한다.
    seen = {}

    class _Proc:
        stdout, stderr, returncode = "결과 OK", "", 0

    def _fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _Proc()

    monkeypatch.setattr(backends.subprocess, "run", _fake_run)
    spec = {"type": "cli", "command": ["claude", "-p", "{prompt}"],
            "parser": "raw", "timeout_sec": 5}
    res = run_backend("claude", spec, "안녕 프롬프트")

    assert seen["kw"].get("stdin") is subprocess.DEVNULL   # 데드락 방지 핵심
    assert "안녕 프롬프트" in seen["cmd"]                    # {prompt} argv 치환
    assert seen["kw"].get("encoding") == "utf-8"            # cp949 깨짐 방지
    assert res.ok and res.text == "결과 OK"


# -------------------------------------- ARIS AD1: 적대적 검수
def test_ensure_cross_family_swaps_reviewer(tmp_path):
    cfg = Config.load(tmp_path)                          # 기본 backend(claude/codex/gemini)
    o = orchestrator.Orchestrator(cfg, auto=True)
    rev = o._ensure_cross_family("claude-main", "claude-main")   # 같은 패밀리 → 교체
    assert cfg.worker(rev)["backend"] != "claude"
    assert o._ensure_cross_family("claude-main", "codex-critic") == "codex-critic"  # 다른 패밀리 유지


def test_adversarial_review_uses_redteam_prompt(mock_root, monkeypatch):
    from yok3x.backends import BackendResult
    prompts = []
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda n, s, p, cwd=None, model=None, effort=None: prompts.append(p) or
                        BackendResult(backend=n, ok=True, text="SCORE: 5\n결함"))
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False
    cfg.yok3x["adversarial_review"] = True
    orchestrator.Orchestrator(cfg, auto=True).run_producer_reviewer(
        "t", "claude-main", "codex-critic", max_rounds=1, pass_score=9)
    assert any("무너뜨리는" in p or "적대적으로 검수" in p for p in prompts)   # red-team 주입


# -------------------------------------- knot 이중레벨 검색(LightRAG식, 의존성0)
def test_knot_query_dual_level_expansion(tmp_path):
    from yok3x import knot
    cfg = Config.load(tmp_path); cfg.ensure_dirs()
    knot.save(cfg, "alpha topic", "about slug conversion. see [[beta note]].", tags=["x"])
    knot.save(cfg, "beta note", "unrelated content here.", tags=["y"])   # 키워드 없음, 링크로만 도달
    knot.save(cfg, "gamma", "shares tag with alpha.", tags=["x"])        # 공유 태그
    names = {n.get("title") for _, n in knot.query(cfg, "slug", expand=True)}
    assert "alpha topic" in names        # 저수준: 키워드 직접 히트
    assert "beta note" in names          # 고수준: [[링크]] 확장으로 도달
    assert {n.get("title") for _, n in knot.query(cfg, "slug", expand=False)} == {"alpha topic"}


def test_knot_recency_decay_weight():
    from yok3x import knot
    from datetime import datetime
    now = datetime(2026, 7, 13, 12, 0, 0)
    assert knot._recency_weight(now.isoformat(), now, 90) == 1.0            # 나이 0 → 감쇠 없음
    old = datetime(2026, 4, 14, 12, 0, 0).isoformat()                       # 90일 전
    assert abs(knot._recency_weight(old, now, 90) - 0.5) < 0.02             # 반감기 → 0.5
    assert knot._recency_weight(old, now, 0) == 1.0                         # halflife 0 → 끔
    assert knot._recency_weight("garbage", now, 90) == 1.0                  # 파싱 실패 → 1.0


def test_knot_recency_ranks_newer_first(tmp_path, monkeypatch):
    from yok3x import knot
    from datetime import datetime
    cfg = Config.load(tmp_path); cfg.ensure_dirs()
    cfg.yok3x["knot"]["recency_halflife_days"] = 30
    knot.save(cfg, "old slug", "slug slug slug", tags=["t"])
    knot.save(cfg, "new slug", "slug slug slug", tags=["t"])
    # old 노트의 created를 과거로 조작
    import re as _re
    for p in cfg.paths.knowledge.glob("old-slug*.md"):
        t = p.read_text(encoding="utf-8")
        p.write_text(_re.sub(r"created: .*", "created: 2026-01-01T00:00:00", t), encoding="utf-8")
    monkeypatch.setattr(knot, "datetime", __import__("datetime").datetime)
    ranked = [n.get("title") for _, n in knot.query(cfg, "slug", expand=False)]
    assert ranked[0] == "new slug"          # 동일 키워드 점수라도 최신이 상위


def test_knot_lint_flags_duplicates(tmp_path):
    from yok3x import knot
    cfg = Config.load(tmp_path); cfg.ensure_dirs()
    knot.save(cfg, "auth login flow", "handles [[session]] and tokens", tags=["auth", "security"])
    knot.save(cfg, "auth login flow v2", "handles [[session]] and tokens", tags=["auth", "security"])
    knot.save(cfg, "unrelated cooking", "pasta recipe", tags=["food"])
    dups = [i for i in knot.lint(cfg) if "중복 후보" in i]
    assert len(dups) == 1 and "cooking" not in dups[0]     # 유사 쌍만 감지


def test_knot_extract_key_points():
    from yok3x import knot
    text = "intro line\nSCORE: 8\n- [x] 엣지케이스 처리\n랜덤 문장\nSELF-CHECK: 통과"
    kp = knot.extract_key_points(text)
    assert "SCORE: 8" in kp and "SELF-CHECK: 통과" in kp and "랜덤 문장" not in kp


# -------------------------------------- 자기오염 루프 방지(계산기 실패 회귀)
def test_finish_does_not_overwrite_brief(mock_root):
    # 런 산출물이 brief.md에 덮여 다음 런 프롬프트를 오염시키던 루프 차단 — _finish는 brief.md를 안 쓴다.
    cfg = Config.load(mock_root)
    (cfg.paths.root / "brief.md").unlink(missing_ok=True)
    tf = mock_root / "t.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "계산기 만들어줘",
                              "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1},
                             ensure_ascii=False), encoding="utf-8")
    run_task_file(cfg, tf, auto=True)
    assert not (cfg.paths.root / "brief.md").exists()          # 런 출력이 brief.md로 새지 않음


def test_context_for_prompt_excludes_run_notes(mock_root):
    from yok3x import knot
    cfg = Config.load(mock_root)
    knot.save(cfg, "run-abc", "작업: 옛날 실패\n요점: 빈 작업입니다", tags=["run"], source="orchestrator")
    knot.save(cfg, "슬러그 규칙", "슬러그는 소문자·하이픈", tags=["note"], source="user")
    out = knot.context_for_prompt(cfg, "빈 작업 슬러그")
    assert "빈 작업입니다" not in out                          # 자동 런 노트는 주입 안 함
    assert "run-abc" not in out


def test_lint_skips_orchestrator_notes(mock_root):
    from yok3x import knot
    cfg = Config.load(mock_root)
    knot.save(cfg, "run-xyz", "요점: [[csv-stream]] 참고", tags=["run"], source="orchestrator")
    assert not [i for i in knot.lint(cfg) if "깨진 링크" in i]  # 런 노트의 [[..]]는 오탐 안 함
    knot.save(cfg, "내 노트", "[[없는링크]] 참조", tags=["note"], source="user")
    assert [i for i in knot.lint(cfg) if "깨진 링크" in i]      # 사용자 노트는 여전히 검사


# -------------------------------------- run_id 충돌 방지(마이크로초)
def test_run_id_includes_microseconds(mock_root):
    import re
    from yok3x.orchestrator import Orchestrator
    o = Orchestrator(Config.load(mock_root))
    assert re.match(r"run_\d{8}_\d{6}_\d{6}$", o.run_id)   # 초 단위 충돌 방지


# -------------------------------------- v3.3 S1: 상황별 모델 프로파일
def test_resolve_model_off_by_default(tmp_path):
    cfg = Config.load(tmp_path)                        # active_profile 기본 ""
    assert orchestrator.resolve_model(cfg, "review") == (None, None, "")


def test_resolve_model_routes_by_profile_and_situation(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = "best"
    b, m, why = orchestrator.resolve_model(cfg, "critic")   # critic→review→fable-5
    assert b == "claude" and m == "claude-fable-5" and "review" in why
    assert orchestrator.resolve_model(cfg, "build")[0] == "codex"          # build→gpt-5.6
    assert orchestrator.resolve_model(cfg, "design_review")[0] == "gemini"  # design→gemini
    assert orchestrator.resolve_model(cfg, "weird")[0] == "claude"          # 미매핑→"*" 폴백


def test_resolve_model_unknown_profile_is_noop(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = "nope"
    assert orchestrator.resolve_model(cfg, "review") == (None, None, "")


# -------------------------------------- v3.3 S3: best 프로파일 argmax 자동 유도
def test_best_profile_derives_from_benchmarks(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = "best"
    # 기본 benchmarks review 최고점 = fable-5
    assert orchestrator.resolve_model(cfg, "critic")[:2] == ("claude", "claude-fable-5")
    # benchmarks만 갱신해도 best가 자동 추종
    cfg.yok3x["benchmarks"]["review"] = {"gpt-5.6": 99.0, "fable-5": 80.0}
    b, m, why = orchestrator.resolve_model(cfg, "critic")
    assert b == "codex" and "review→gpt-5.6" in why
    # 벤치마크 없는 상황은 "*"로 폴백
    assert orchestrator.resolve_model(cfg, "weird")[0] == "claude"


# -------------------------------------- v3.3 S2: 가용성·한도 필터
def test_backend_available_installed_and_headroom(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    monkeypatch.setattr(usage.shutil, "which", lambda x: None)          # 미설치
    assert usage.backend_available(cfg, "claude") is False
    monkeypatch.setattr(usage.shutil, "which", lambda x: "/bin/" + x)   # 설치됨
    monkeypatch.setattr(usage, "check_backend",
                        lambda c, b: usage.GuardVerdict(b, 0.2, "5h", "ok", "d"))
    assert usage.backend_available(cfg, "claude") is True
    monkeypatch.setattr(usage, "check_backend",
                        lambda c, b: usage.GuardVerdict(b, 1.0, "5h", "stop", "d"))
    assert usage.backend_available(cfg, "claude") is False              # 한도 stop


def test_resolve_model_s2_falls_back_to_next_available(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = "best"                 # review→fable-5(claude)
    # claude 전부 불가 → review benchmarks 다음 순위 중 가용한 gpt-5.6(codex)
    b, m, why = orchestrator.resolve_model(cfg, "critic", available=lambda bk: bk != "claude")
    assert b == "codex" and "폴백" in why
    # 전부 가용 → 프로파일 픽 그대로(폴백 아님)
    b2, m2, why2 = orchestrator.resolve_model(cfg, "critic", available=lambda bk: True)
    assert b2 == "claude" and m2 == "claude-fable-5" and "폴백" not in why2
    # 전부 불가 → 오버라이드 없음
    assert orchestrator.resolve_model(cfg, "critic", available=lambda bk: False) == (None, None, "")


# -------------------------------------- v3.2 P2: 백엔드 폴오버(on/off)
def test_failover_backend_off_by_default(tmp_path):
    cfg = Config.load(tmp_path)                       # failover_enabled 기본 False
    cfg.yok3x["guard"]["degrade"]["offline_enabled"] = False   # P3도 끄면 대안 없음(P3는 별도 테스트)
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) is None


def test_failover_backend_picks_freest_and_respects_limits(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["degrade"]["failover_enabled"] = True
    monkeypatch.setattr(usage.shutil, "which", lambda x: "/bin/" + x)   # 전부 설치
    ratios = {"claude": 0.99, "codex": 0.1, "gemini": 0.5}
    monkeypatch.setattr(usage, "check_backend",
                        lambda c, b: usage.GuardVerdict(b, ratios.get(b, 0.0), "5h", "ok", "d"))
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) == "codex"   # 최소 ratio
    assert usage.failover_backend(cfg, "claude-main", "claude", 3) is None      # 런당 상한
    cfg.yok3x["guard"]["degrade"]["roles_no_failover"] = ["claude-main"]
    assert usage.failover_backend(cfg, "claude-main", "claude", 0) is None      # 역할 제외


def test_call_worker_fails_over_when_stopped(tmp_path, monkeypatch):
    from yok3x.config import scaffold
    from yok3x.backends import BackendResult
    scaffold(tmp_path)
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["degrade"]["failover_enabled"] = True
    monkeypatch.setattr(usage.shutil, "which", lambda x: "/bin/" + x)
    monkeypatch.setattr(usage, "check_backend", lambda c, b: usage.GuardVerdict(
        b, 1.0 if b == "claude" else 0.1, "5h", "stop" if b == "claude" else "ok", "d"))
    cap = {}
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda n, s, p, cwd=None, model=None, effort=None: cap.update(backend=n) or
                        BackendResult(backend=n, ok=True, text="x"))
    o = orchestrator.Orchestrator(cfg, auto=True)
    o.call_worker("claude-main", "t", "build")     # claude stop → 폴오버
    assert cap["backend"] != "claude"              # 다른 도구로 전환됨
    assert o._failover_map.get("claude-main") == cap["backend"]   # sticky 기록


def test_call_worker_aborts_on_stop_without_failover(tmp_path, monkeypatch):
    from yok3x.config import scaffold
    scaffold(tmp_path)
    cfg = Config.load(tmp_path)                     # failover off(기본)
    cfg.yok3x["guard"]["degrade"]["offline_enabled"] = False   # P3 오프라인 폴백도 꺼야 순수 정지
    monkeypatch.setattr(usage, "check_backend",
                        lambda c, b: usage.GuardVerdict(b, 1.0, "5h", "stop", "d"))
    o = orchestrator.Orchestrator(cfg, auto=True)
    with pytest.raises(orchestrator.RunAborted):    # 폴오버·오프라인 모두 off → 현행처럼 정지
        o.call_worker("claude-main", "t", "build")


def test_manual_worker_model_used_when_profile_off(mock_root, monkeypatch):
    from yok3x.backends import BackendResult
    cap = {}
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda n, s, p, cwd=None, model=None, effort=None: cap.update(model=model) or
                        BackendResult(backend=n, ok=True, text="x"))
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False
    cfg.yok3x["active_profile"] = ""                       # off → 수동 모델 적용
    cfg.yok3x["workers"]["claude-main"]["model"] = "claude-opus-4-8"
    orchestrator.Orchestrator(cfg, auto=True).call_worker("claude-main", "t", "build")
    assert cap["model"] == "claude-opus-4-8"


def test_profile_overrides_manual_model(mock_root, monkeypatch):
    from yok3x.backends import BackendResult
    cap = {}
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda n, s, p, cwd=None, model=None, effort=None: cap.update(model=model) or
                        BackendResult(backend=n, ok=True, text="x"))
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False
    cfg.yok3x["active_profile"] = "best"                   # on → 프로파일이 수동보다 우선
    cfg.yok3x["workers"]["claude-main"]["model"] = "claude-opus-4-8"
    orchestrator.Orchestrator(cfg, auto=True).call_worker("claude-main", "t", "critic")
    assert cap["model"] == "claude-fable-5"                # review→fable-5(프로파일)


def test_call_worker_applies_profile_routing(mock_root, monkeypatch):
    from yok3x.backends import BackendResult
    cap = {}

    def spy(name, spec, prompt, cwd=None, model=None, effort=None):
        cap["backend"], cap["model"], cap["effort"] = name, model, effort
        return BackendResult(backend=name, ok=True, text="x")

    monkeypatch.setattr(orchestrator, "run_backend", spy)
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["use_real_limits"] = False       # 결정적(네트워크 X)
    cfg.yok3x["active_profile"] = "best"
    o = orchestrator.Orchestrator(cfg, auto=True)
    o.call_worker("claude-main", "task", "critic")      # review 상황 → fable-5
    assert cap["backend"] == "claude" and cap["model"] == "claude-fable-5"


# -------------------------------------- 적응형 열화 P1: 모델 다운그레이드
def _verdict(ratio, level="warn", backend="claude"):
    return usage.GuardVerdict(backend, ratio, "5h", level, "detail")


def test_degrade_plan_downgrades_producer_near_limit(tmp_path):
    cfg = Config.load(tmp_path)                       # 기본값: claude-main→claude, lite=haiku
    cfg.yok3x["guard"]["degrade"] = {"enabled": True, "downgrade_ratio": 0.9,
                                     "roles_no_downgrade": ["codex-critic"]}
    a, m = usage.degrade_plan(cfg, "claude-main", _verdict(0.95))
    assert a == "downgrade" and m and "haiku" in m    # 한도 근처 → 가벼운 모델
    assert usage.degrade_plan(cfg, "claude-main", _verdict(0.5))[0] == "normal"   # 여유
    assert usage.degrade_plan(cfg, "codex-critic", _verdict(0.99))[0] == "normal"  # 리뷰어 제외
    cfg.yok3x["guard"]["degrade"]["enabled"] = False
    assert usage.degrade_plan(cfg, "claude-main", _verdict(0.99))[0] == "normal"   # opt-out


def test_run_cli_injects_model_arg_only_when_model_given(monkeypatch):
    seen = {}

    class _P:
        stdout, stderr, returncode = '{"result":"ok"}', "", 0

    def _fake(cmd, **kw):
        seen["cmd"] = cmd
        return _P()

    monkeypatch.setattr(backends.subprocess, "run", _fake)
    spec = {"type": "cli", "command": ["claude", "-p", "{prompt}"],
            "model_arg": ["--model", "{model}"], "parser": "raw"}
    run_backend("claude", spec, "hi", model="claude-haiku-4-5-20251001")
    assert "--model" in seen["cmd"] and "claude-haiku-4-5-20251001" in seen["cmd"]
    seen.clear()
    run_backend("claude", spec, "hi")                 # model 없으면 미주입
    assert "--model" not in seen["cmd"]


def test_cli_backend_passes_multiline_prompt_via_stdin(monkeypatch):
    # {prompt}가 argv에 없으면 프롬프트를 stdin으로 넘긴다 — Windows .cmd 심의 멀티라인
    # argv 잘림(첫 줄바꿈에서 절단)을 우회하는 핵심 수정.
    seen = {}

    class _P:
        stdout, stderr, returncode = '{"result":"ok"}', "", 0

    def _fake(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _P()

    monkeypatch.setattr(backends.subprocess, "run", _fake)
    spec = {"type": "cli", "command": ["claude", "-p", "--output-format", "json"],
            "parser": "raw"}
    ml = "[작업]\n여러 줄\n프롬프트"
    run_backend("claude", spec, ml)
    assert ml not in seen["cmd"]                    # argv에는 프롬프트가 없고
    assert seen["kw"].get("input") == ml           # stdin(input)으로 온전히 전달
    assert "stdin" not in seen["kw"]               # DEVNULL 아님


# ------------------------------------------------ verify_cmd 전역 상속/재정의
def _pr_spec(workdir, verify_cmd=None):
    s = {"pattern": "producer-reviewer", "task": "t", "producer": "claude-main",
         "reviewer": "codex-critic", "max_rounds": 1, "pass_score": 8.0,
         "workdir": str(workdir)}
    if verify_cmd is not None:
        s["verify_cmd"] = verify_cmd
    return s


def test_verify_cmd_inherited_from_config_and_runs(mock_root, tmp_path):
    # 전역 verify_cmd가 task에 없을 때 상속되어 '실제로' 실행되는지 센티넬로 확인.
    (tmp_path / "mk.py").write_text("open('ran.txt','w').close()", encoding="utf-8")
    cfg = Config.load(mock_root)
    cfg.yok3x["verify_cmd"] = 'python "mk.py"'
    tf = mock_root / "task.json"
    tf.write_text(json.dumps(_pr_spec(tmp_path), ensure_ascii=False), encoding="utf-8")
    run_task_file(cfg, tf, auto=True)
    assert (tmp_path / "ran.txt").exists()          # 전역 게이트가 돌았다


def test_task_verify_cmd_overrides_config(mock_root, tmp_path):
    # task의 verify_cmd가 전역보다 우선(전역 것은 안 돌아야 한다).
    (tmp_path / "cfg.py").write_text("open('cfg.txt','w').close()", encoding="utf-8")
    (tmp_path / "task.py").write_text("open('task.txt','w').close()", encoding="utf-8")
    cfg = Config.load(mock_root)
    cfg.yok3x["verify_cmd"] = 'python "cfg.py"'
    tf = mock_root / "task.json"
    tf.write_text(json.dumps(_pr_spec(tmp_path, 'python "task.py"'), ensure_ascii=False),
                  encoding="utf-8")
    run_task_file(cfg, tf, auto=True)
    assert (tmp_path / "task.txt").exists() and not (tmp_path / "cfg.txt").exists()


# ------------------------------------------------ 전역 워크스페이스(기본 workdir)
def test_run_inherits_global_workspace(mock_root, tmp_path):
    cfg = Config.load(mock_root)
    ws = tmp_path / "ws"; ws.mkdir()
    cfg.yok3x["workspace"] = str(ws)          # task에 workdir 없음 → 전역 상속
    tf = mock_root / "t.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t",
        "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1}),
        encoding="utf-8")
    assert run_task_file(cfg, tf, auto=True) == "done"


def test_run_aborts_on_bad_global_workspace(mock_root):
    cfg = Config.load(mock_root)
    cfg.yok3x["workspace"] = "/definitely/no/such/dir/xyz123"
    tf = mock_root / "t.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t",
        "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1}),
        encoding="utf-8")
    assert run_task_file(cfg, tf, auto=True).startswith("aborted")   # 명시 중단


def test_task_file_with_bom_runs(mock_root):
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "t", "producer": "claude-main",
            "reviewer": "codex-critic", "max_rounds": 1, "pass_score": 8.0}
    tf = mock_root / "task.json"
    tf.write_text("﻿" + json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert run_task_file(cfg, tf, auto=True) == "done"


# ------------------------------------------------ C-1 준비/실행 분리 + 원자적 상태 저장
def test_prepare_call_has_no_execution_side_effects(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    orch = Orchestrator(cfg, auto=False)
    monkeypatch.setattr(
        usage, "check_backend",
        lambda *a, **k: pytest.fail("prepare_call에서 가드를 호출하면 안 됨"))
    monkeypatch.setattr(
        orch, "_gate",
        lambda *a, **k: pytest.fail("prepare_call에서 게이트를 호출하면 안 됨"))

    spec = orch.prepare_call("claude-main", "준비만", "build")

    assert isinstance(spec, orchestrator.CallSpec)
    assert orch._step_i == 0 and orch.steps == []
    assert not orch.run_dir.exists()
    assert not Path(spec.run_cwd).exists()


def test_prepare_call_is_deterministic(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    orch = Orchestrator(cfg, auto=True)

    first = orch.prepare_call("claude-main", "같은 작업", "critic", "추가 문맥")
    second = orch.prepare_call("claude-main", "같은 작업", "critic", "추가 문맥")

    assert first == second
    assert (first.backend, first.model, first.prompt, first.run_cwd) == (
        second.backend, second.model, second.prompt, second.run_cwd)


def test_prepare_call_reflects_cwd_read_only_and_task_kind(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    orch = Orchestrator(cfg, auto=True)
    cwd = str(tmp_path / "target")

    spec = orch.prepare_call(
        "codex-main", "읽기 조사", task_kind="critic",
        extra_context="근거", cwd=cwd, read_only=True)

    assert spec.worker == "codex-main" and spec.task == "읽기 조사"
    assert spec.task_kind == "critic" and spec.extra_context == "근거"
    assert spec.cwd == cwd and spec.run_cwd == cwd and spec.read_only is True
    assert spec.index is None


def test_call_worker_keeps_guard_gate_and_artifact_flow(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    cfg.yok3x["active_profile"] = ""
    events = []
    verdict = usage.GuardVerdict("claude", 0.1, "5h", "ok", "여유")
    monkeypatch.setattr(
        usage, "check_backend",
        lambda cfg, backend: events.append(("guard", backend)) or verdict)
    monkeypatch.setattr(usage, "record", lambda *a, **k: events.append(("record",)))

    def fake_backend(name, backend_spec, prompt, **kwargs):
        events.append(("backend", name, prompt, kwargs))
        return BackendResult(
            backend=name, ok=True, text="SCORE: 8\n완료",
            total_tokens=12, cost_usd=0.25, duration_ms=34)

    monkeypatch.setattr(orchestrator, "run_backend", fake_backend)
    orch = Orchestrator(
        cfg, auto=False,
        ask=lambda message: events.append(("gate", message)) or "y")

    result = orch.call_worker(
        "claude-main", "회귀 작업", "critic",
        extra_context="검토 대상", cwd=str(tmp_path), read_only=True)

    assert result.ok and orch._step_i == 1
    assert [event[0] for event in events] == ["guard", "gate", "backend", "record"]
    assert orch.steps[0].status == "done" and orch.steps[0].score == 8.0
    assert orch.steps[0].tokens == 12 and orch.steps[0].cost_usd == 0.25
    step = json.loads((orch.run_dir / "step_01_claude-main.json").read_text(encoding="utf-8"))
    status = json.loads((orch.run_dir / "status.json").read_text(encoding="utf-8"))
    assert step["task"] == "회귀 작업" and step["usage"]["duration_ms"] == 34
    assert status["state"] == "running" and status["steps"][0]["status"] == "done"
    backend_event = events[2]
    assert backend_event[3]["read_only"] is True and backend_event[3]["cwd"] == str(tmp_path)


def test_save_status_uses_atomic_replace_without_temp_residue(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    orch = Orchestrator(cfg, auto=True)
    real_replace = orchestrator.os.replace
    replaced = []

    def spy_replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.exists() and source.parent == destination.parent
        assert source.name.startswith(".status.json.") and source.suffix == ".tmp"
        replaced.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(orchestrator.os, "replace", spy_replace)
    orch._save_status("running", {"marker": "원자적"})

    status_path = orch.run_dir / "status.json"
    assert replaced and replaced[0][1] == status_path
    assert json.loads(status_path.read_text(encoding="utf-8"))["marker"] == "원자적"
    assert list(orch.run_dir.glob("*.tmp")) == []
    assert list(orch.run_dir.glob(".status.json.*")) == []


def test_save_acquire_uses_atomic_replace_without_temp_residue(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    orch = Orchestrator(cfg, auto=True)
    real_replace = orchestrator.os.replace
    replaced = []

    def spy_replace(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.exists() and source.parent == destination.parent
        assert source.name.startswith(".acquire.json.") and source.suffix == ".tmp"
        replaced.append(destination)
        real_replace(source, destination)

    monkeypatch.setattr(orchestrator.os, "replace", spy_replace)
    orch._save_acquire([{"answer": "a"}], [{"question": "q"}])

    acquire_path = orch.run_dir / "acquire.json"
    assert replaced == [acquire_path]
    saved = json.loads(acquire_path.read_text(encoding="utf-8"))
    assert saved["qa_items"] == [{"answer": "a"}]
    assert list(orch.run_dir.glob("*.tmp")) == []
    assert list(orch.run_dir.glob(".acquire.json.*")) == []


def test_rename_task_same_name_updates_label_in_place(tmp_path):
    """사용자 보고 버그: 이름 수정 프롬프트가 현재 이름을 미리 채워주는데, 그대로 확인만 눌러도
    '같은 이름의 작업이 이미 있다'로 실패했다. 자기 자신으로의 rename은 충돌이 아니다."""
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    gs._save_task(cfg, "내작업", {"label": "내작업", "task": "진짜 목표",
                                 "pattern": "producer-reviewer", "workdir": "F:/x"})

    got = gs._rename_task(cfg, "task-내작업.json", "내작업")

    assert got.get("ok"), got
    assert (tmp_path / "task-내작업.json").exists()          # 파일이 지워지면 안 됨
    spec = gs._load_task(cfg, "task-내작업.json")["spec"]
    assert spec["task"] == "진짜 목표" and spec["workdir"] == "F:/x"   # 내용 보존


# --- 산출물 게시(artifacts) — codex 공동검토 설계 ---
def test_artifacts_parse_only_file_fences():
    from yok3x import artifacts as A
    text = "```file:index.html\n<h1>hi</h1>\n```\n```html\n<div>ignored</div>\n```"
    blocks = A.parse_file_blocks(text)
    assert [b.path for b in blocks] == ["index.html"]      # 언어펜스(추측)는 제외
    assert blocks[0].content == "<h1>hi</h1>"


def test_artifacts_file_fence_crlf_does_not_leave_carriage_return():
    from yok3x import artifacts as A

    blocks = A.parse_file_blocks("```file:x.txt\r\nfirst\r\nsecond\r\n```\r\n")

    assert blocks == [A.FileBlock("x.txt", "first\r\nsecond")]


def test_artifacts_reject_dangerous_paths():
    from yok3x import artifacts as A
    bad = ["../evil", "/etc/passwd", "C:/x", "a/../b", "foo/", "CON", "aux.log", "con.txt ", "x\ty"]
    plan = A.plan_files([A.FileBlock(p, "x") for p in bad])
    assert plan.accepted == []                              # 전부 거부
    assert len(plan.rejected) == len(bad)


def test_artifacts_overwrite_and_case_collision():
    from yok3x import artifacts as A
    # 기본은 덮어쓰기 금지
    p = A.plan_files([A.FileBlock("a.txt", "n")], existing={"a.txt"})
    assert not p.accepted and "덮어쓰기" in p.rejected[0]["reason"]
    # overwrite=True면 허용
    assert A.plan_files([A.FileBlock("a.txt", "n")], existing={"a.txt"}, overwrite=True).accepted
    # 대소문자 충돌: 하나만 통과
    c = A.plan_files([A.FileBlock("App.js", "a"), A.FileBlock("app.js", "b")])
    assert len(c.accepted) == 1 and c.rejected


def test_materialize_writes_and_blocks_escape(tmp_path):
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    cfg.yok3x["auto_approve"] = True
    wd = tmp_path / "proj"; wd.mkdir()
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    o.materialize = {"enabled": True}
    final = ("```file:index.html\n<h1>c</h1>\n```\n"
             "```file:src/app.js\ncode\n```\n"
             "```file:../hack.js\nevil\n```")
    res = o._materialize_outputs(final)
    root = Path(res["root"])
    got = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())
    assert got == ["index.html", "src/app.js"]             # 정상 2개만
    assert (root / "index.html").read_text(encoding="utf-8").strip() == "<h1>c</h1>"
    assert not (wd.parent / "hack.js").exists()            # 경로탈출 차단
    assert res["ok"] and len(res["written"]) == 2
    assert any("hack" in r["path"] for r in res["rejected"])


def test_materialize_disabled_by_default(tmp_path):
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(tmp_path)
    # materialize 미설정이면 아무것도 안 함
    assert o._materialize_outputs("```file:x.txt\ny\n```") == {"enabled": False}
    assert not (tmp_path / "yok3x-out").exists()


def test_review_changes_disabled_preserves_existing_behavior(tmp_path):
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    wd = tmp_path / "proj"; wd.mkdir()
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)

    assert o._review_changes("```file:x.txt\ny\n```") == {"enabled": False}
    assert not (wd / "yok3x-out").exists()


def test_review_bundle_status_diff_hashes_and_read_only_base(tmp_path):
    import difflib
    import hashlib
    from yok3x import orchestrator as O

    cfg = Config.load(tmp_path)
    wd = tmp_path / "proj"; wd.mkdir()
    original_modified = b"old\n"
    original_same = "같음\n".encode("utf-8")
    (wd / "modified.txt").write_bytes(original_modified)
    (wd / "same.txt").write_bytes(original_same)
    before = {p.name: p.read_bytes() for p in wd.iterdir() if p.is_file()}

    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    o.changes = {"mode": "review"}
    final = ("```file:new.txt\nfresh\n\n```\n"
             "```file:modified.txt\nnew\n\n```\n"
             "```file:same.txt\n같음\n\n```")
    result = o._review_changes(final)
    root = Path(result["root"])
    bundle = json.loads((root / "changes.json").read_text(encoding="utf-8"))
    records = {item["path"]: item for item in bundle["files"]}

    assert result["files"] == [
        {"path": "new.txt", "status": "new"},
        {"path": "modified.txt", "status": "modified"},
        {"path": "same.txt", "status": "unchanged"},
    ]
    assert records["new.txt"] == {
        "path": "new.txt", "status": "new", "base_sha256": None,
        "proposed_sha256": hashlib.sha256(b"fresh\n").hexdigest(),
        "base_bytes": 0, "proposed_bytes": len(b"fresh\n"),
    }
    assert records["modified.txt"] == {
        "path": "modified.txt", "status": "modified",
        "base_sha256": hashlib.sha256(original_modified).hexdigest(),
        "proposed_sha256": hashlib.sha256(b"new\n").hexdigest(),
        "base_bytes": len(original_modified), "proposed_bytes": len(b"new\n"),
    }
    assert records["same.txt"]["base_sha256"] == hashlib.sha256(original_same).hexdigest()
    assert records["same.txt"]["proposed_sha256"] == hashlib.sha256(original_same).hexdigest()
    assert records["same.txt"]["base_bytes"] == records["same.txt"]["proposed_bytes"]

    expected = "".join(difflib.unified_diff(
        [], ["fresh\n"], fromfile="a/new.txt", tofile="b/new.txt"))
    expected += "".join(difflib.unified_diff(
        ["old\n"], ["new\n"], fromfile="a/modified.txt", tofile="b/modified.txt"))
    assert (root / "changes.diff").read_text(encoding="utf-8") == expected
    assert (root / "new.txt").read_bytes() == b"fresh\n"
    assert (root / "modified.txt").read_bytes() == b"new\n"
    assert (root / "same.txt").read_bytes() == original_same
    assert {p.name: p.read_bytes() for p in wd.iterdir() if p.is_file()} == before


def test_review_bundle_rejects_escape_and_finish_records_summary(tmp_path, monkeypatch):
    from yok3x import orchestrator as O

    cfg = Config.load(tmp_path)
    wd = tmp_path / "proj"; wd.mkdir()
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    o.changes = {"mode": "review"}
    monkeypatch.setattr(orchestrator.knot, "save", lambda *args, **kwargs: None)

    o._finish("review", "```file:ok.txt\nok\n```\n```file:../escape.txt\nbad\n```")
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    root = Path(status["changes"]["root"])
    bundle = json.loads((root / "changes.json").read_text(encoding="utf-8"))

    assert status["changes"] == {
        "mode": "review",
        "files": [{"path": "ok.txt", "status": "new"}],
        "root": str(root),
    }
    assert any(item["path"] == "../escape.txt" for item in bundle["rejected"])
    assert not (tmp_path / "escape.txt").exists()
    assert "[changes] 검토 번들 1파일(new=1/modified=0/unchanged=0)" in (
        o.run_dir / "run.log").read_text(encoding="utf-8")


def test_review_bundle_isolated_root_and_no_newline_diff(tmp_path):
    from yok3x import orchestrator as O

    cfg = Config.load(tmp_path)
    wd = tmp_path / "proj"; wd.mkdir()
    victim = wd / "victim.txt"
    victim.write_bytes(b"old")
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    # review가 materialize.root를 재사용하면 victim을 덮어쓰는 치명적 회귀다.
    o.materialize = {"enabled": True, "root": str(wd), "overwrite": True}
    o.changes = {"mode": "review"}

    materialized = o._materialize_outputs(
        "```file:victim.txt\nnew\n```\n```file:changes.json\ncollision\n```")
    result = o._review_changes("```file:victim.txt\nnew\n```")
    root = Path(result["root"])

    assert root == wd / "yok3x-out" / o.run_id
    assert Path(materialized["root"]) == root
    assert any(item["path"] == "changes.json" for item in materialized["rejected"])
    assert victim.read_bytes() == b"old"
    assert (root / "victim.txt").read_bytes() == b"new"
    assert (root / "changes.diff").read_text(encoding="utf-8") == (
        "--- a/victim.txt\n"
        "+++ b/victim.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n")


def test_review_bundle_rejects_symlinked_base_path(tmp_path, monkeypatch):
    from yok3x import orchestrator as O

    cfg = Config.load(tmp_path)
    wd = tmp_path / "proj"; wd.mkdir()
    linked = wd / "linked"; linked.mkdir()
    target = linked / "victim.txt"; target.write_bytes(b"outside")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink",
        lambda path: path == linked or real_is_symlink(path))
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    o.changes = {"mode": "review"}

    result = o._review_changes("```file:linked/victim.txt\nchanged\n```")
    bundle = json.loads(
        (Path(result["root"]) / "changes.json").read_text(encoding="utf-8"))

    assert result["files"] == []
    assert any("심볼릭" in item["reason"] for item in bundle["rejected"])
    assert target.read_bytes() == b"outside"


def test_review_mode_enables_file_block_prompt_contract(tmp_path):
    from yok3x import artifacts as A
    from yok3x import orchestrator as O

    cfg = Config.load(tmp_path)
    o = O.Orchestrator(cfg, auto=True)
    o.changes = {"mode": "review"}

    assert A.FILES_CONTRACT in o.prepare_call("claude-main", "build", "build").prompt


# ------------------------------------------------ review bundle 사람 결정 CLI (F1-g)
def _make_cli_review_bundle(workdir, files, run_id="run_review_cli"):
    """files={path: proposed_text}; 현재 workdir 내용을 base로 실제 F1-d 번들을 만든다."""
    from yok3x import orchestrator as O

    cfg = Config.load(workdir)
    o = O.Orchestrator(cfg, auto=True)
    o.run_id = run_id
    o.run_dir = cfg.paths.runs / run_id
    o.workdir = str(workdir)
    o.changes = {"mode": "review"}
    blocks = "\n".join(f"```file:{path}\n{text}\n```" for path, text in files.items())
    return Path(o._review_changes(blocks)["root"])


def test_review_decision_is_pure_for_hash_collision_and_paths():
    import hashlib
    from yok3x.review import decide_file_application

    base = hashlib.sha256(b"old").hexdigest()
    proposed = hashlib.sha256(b"new").hexdigest()
    modified = {"path": "src/app.py", "status": "modified",
                "base_sha256": base, "proposed_sha256": proposed}
    common = {"current_exists": True, "candidate_sha256": proposed}

    assert decide_file_application(
        modified, current_sha256=base, **common).allowed
    assert not decide_file_application(
        modified, current_sha256=hashlib.sha256(b"stale").hexdigest(), **common).allowed
    assert not decide_file_application(
        {"path": "new.py", "status": "new", "base_sha256": None,
         "proposed_sha256": proposed}, current_exists=True,
        current_sha256=None, candidate_sha256=proposed).allowed
    assert not decide_file_application(
        {**modified, "path": "../escape.py"}, current_sha256=base, **common).allowed
    assert not decide_file_application(
        modified, current_sha256=base, path_has_symlink=True, **common).allowed


def test_review_cli_show_is_read_only_and_prints_status_diff(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    target = tmp_path / "app.txt"
    target.write_bytes(b"old")
    root = _make_cli_review_bundle(tmp_path, {"app.txt": "new"})
    before = {p.relative_to(tmp_path): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.chdir(tmp_path)

    assert cli.main(["review", root.name]) == 0
    output = capsys.readouterr().out
    after = {p.relative_to(tmp_path): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert "[modified] app.txt" in output
    assert "--- a/app.txt" in output and "+new" in output


def test_review_cli_accept_matching_base_is_atomic(tmp_path, monkeypatch, capsys):
    from yok3x import cli, review as review_module

    target = tmp_path / "app.txt"
    target.write_bytes(b"old")
    root = _make_cli_review_bundle(tmp_path, {"app.txt": "new"})
    real_replace = review_module.os.replace
    replaced = []

    def observing_replace(source, destination):
        replaced.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(review_module.os, "replace", observing_replace)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["review", root.name, "--accept", "app.txt"]) == 0

    assert target.read_bytes() == b"new"
    assert any(destination == target and source.parent == target.parent
               for source, destination in replaced)
    assert "요약: 적용 1 · 중단 0 · 스킵 0" in capsys.readouterr().out


def test_review_cli_accept_is_all_settled_on_stale_file(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    stale = tmp_path / "stale.txt"
    good = tmp_path / "good.txt"
    stale.write_bytes(b"old-a")
    good.write_bytes(b"old-b")
    root = _make_cli_review_bundle(
        tmp_path, {"stale.txt": "new-a", "good.txt": "new-b"})
    stale.write_bytes(b"changed-after-bundle")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["review", root.name, "--accept"]) == 1
    assert stale.read_bytes() == b"changed-after-bundle"
    assert good.read_bytes() == b"new-b"
    output = capsys.readouterr().out
    assert "stale.txt — 현재 파일의 base 해시 불일치" in output
    assert "요약: 적용 1 · 중단 1 · 스킵 0" in output


def test_review_cli_accept_new_collision_does_not_overwrite(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    root = _make_cli_review_bundle(tmp_path, {"new.txt": "candidate"})
    target = tmp_path / "new.txt"
    target.write_bytes(b"created-later")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["review", root.name, "--accept"]) == 1
    assert target.read_bytes() == b"created-later"
    assert "신규 대상이 이미 존재함" in capsys.readouterr().out


def test_review_cli_rejects_escape_and_symlink_candidate(tmp_path, monkeypatch, capsys):
    import hashlib
    from yok3x import cli

    root = _make_cli_review_bundle(tmp_path, {"linked.txt": "candidate"})
    manifest_path = root / "changes.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = b"escape"
    manifest["files"].append({
        "path": "../escape.txt", "status": "new", "base_sha256": None,
        "proposed_sha256": hashlib.sha256(raw).hexdigest(),
        "base_bytes": 0, "proposed_bytes": len(raw),
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (root.parent / "escape.txt").write_bytes(raw)
    linked_candidate = root / "linked.txt"
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink",
        lambda path: path == linked_candidate or real_is_symlink(path))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["review", root.name, "--accept"]) == 1
    output = capsys.readouterr().out
    assert "linked.txt — 후보 경로에 심볼릭 링크" in output
    assert "../escape.txt — 위험한 경로" in output
    assert not (tmp_path.parent / "escape.txt").exists()
    assert "요약: 적용 0 · 중단 2 · 스킵 0" in output


def test_review_cli_reject_records_only_in_bundle(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    target = tmp_path / "app.txt"
    target.write_bytes(b"old")
    root = _make_cli_review_bundle(tmp_path, {"app.txt": "new"})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["review", root.name, "--reject"]) == 0
    assert target.read_bytes() == b"old"
    assert (root / "app.txt").read_bytes() == b"new"
    assert "rejected run_id=" in (root / "review.log").read_text(encoding="utf-8")
    assert "거절됨" in capsys.readouterr().out


def test_review_bundle_discovery_supports_run_dir_base(tmp_path):
    from yok3x import review as review_module

    cfg = Config.load(tmp_path)
    run_id = "run_without_workdir"
    root = cfg.paths.runs / run_id / "yok3x-out" / run_id
    root.mkdir(parents=True)
    (root / "changes.json").write_text(
        json.dumps({"mode": "review", "files": [], "rejected": []}), encoding="utf-8")
    (root / "changes.diff").write_text("", encoding="utf-8")

    assert review_module.find_bundle(cfg, run_id) == root


def test_daily_pace_catch_up_cap(tmp_path):
    """유동(catch-up) 하루 상한: 덜 썼으면 상한↑(안전캡 2×q), 많이 썼으면↓, 폴백은 고정 q.
    사용자 시나리오: 2일 지나고 0% 사용 → 오늘 28%p(=이틀치 몰아쓰기, 안전캡)."""
    import time
    from yok3x import usage
    def status(current, days_left, strategy="catch_up"):
        cfg = Config.load(tmp_path / f"{strategy}-{current}-{days_left}")
        cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                            "mode": "warn", "strategy": strategy}
        return usage.daily_pace_status(cfg, "claude", current,
                                       reset_at=time.time() + days_left * 86400)
    assert round(status(0.0, 5)["cap"]) == 28       # 3일차·0% → 안전캡 2×14
    assert round(status(0.0, 6.5)["cap"]) == 14      # 1일차·0% → 14(안전캡 미도달)
    assert round(status(30.0, 5)["cap"]) == 12       # 3일차·30% → 42-30
    assert round(status(50.0, 5)["cap"]) == 0        # 과사용 → 0
    assert round(status(0.0, 0.5)["cap"]) == 28      # 마지막 날 → 폭발 방지(안전캡)
    assert round(status(0.0, 5, "fixed")["cap"]) == 14  # fixed는 고정
    # reset_at 없으면 고정 폴백
    cfg = Config.load(tmp_path / "nofb")
    cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                        "mode": "warn", "strategy": "catch_up"}
    assert round(usage.daily_pace_status(cfg, "claude", 0.0, reset_at=None)["cap"]) == 14


def test_daily_pace_strategy_change_applies_same_day(tmp_path):
    """하루 중 fixed→catch_up 전환 시 당일 재초기화 없이 즉시 상한이 유동으로 바뀐다(사용자 UX)."""
    import time
    from yok3x import usage
    cfg = Config.load(tmp_path)
    dp = cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                             "mode": "warn", "strategy": "fixed"}
    reset_at = time.time() + 5 * 86400                       # 3일차
    s1 = usage.daily_pace_status(cfg, "claude", 0.0, reset_at=reset_at)
    assert round(s1["cap"]) == 14                            # fixed
    dp["strategy"] = "catch_up"                              # 같은 날 전략만 변경
    s2 = usage.daily_pace_status(cfg, "claude", 0.0, reset_at=reset_at)
    assert round(s2["cap"]) == 28                            # 즉시 유동 반영(안전캡)
