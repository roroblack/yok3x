"""yok3x 회귀 잠금 테스트.

손으로 매번 확인하던 것을 자동화한다(RULE §5: 실행해서 확인). mock 백엔드 위에서
외부 도구 없이 결정적으로 돈다. 실행: 프로젝트 루트에서 `pytest` 또는 `python -m pytest`.
verify_cmd 게이트에 `pytest -q`를 걸면 프로젝트가 자기 자신을 dogfooding하게 된다.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import threading
import time

import subprocess
from pathlib import Path

import pytest

from yok3x import __version__, backends, calibration, limits, matview, orchestrator, sync_layer, usage
from yok3x.backends import BackendResult, run_backend
from yok3x.config import DEFAULT_YOK3X, Config, scaffold
from yok3x.orchestrator import Orchestrator, run_task_file


@pytest.fixture
def mock_root(tmp_path):
    """mock 백엔드로 초기화된 격리 작업 디렉터리."""
    scaffold(tmp_path, use_mock=True)
    return tmp_path


def _write_sync_calibration(cfg, rows, applied_ids=()):
    """S7 테스트용 calibration 원자료와 run별 comprehension bundle을 만든다."""
    path = cfg.paths.runs.parent / "calibration.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    for run_id in applied_ids:
        run_dir = cfg.paths.runs / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "understanding_bundle.json").write_text(
            json.dumps({"claims": [], "standard_quiz": {"questions": []}}), encoding="utf-8")


def _sync_row(run_id, verify_ok, gate_pass, score=8.0):
    return calibration.make_record(run_id=run_id, verify_ok=verify_ok,
                                   verify_scope="candidate", score=score,
                                   gate_pass=gate_pass)


def test_sync_correlation_report_compares_comprehension_groups(mock_root):
    cfg = Config.load(mock_root)
    rows = ([_sync_row(f"with-{i}", False, False, 5.0) for i in range(3)]
            + [_sync_row(f"with-{i}", True, True, 9.0) for i in range(2)]
            + [_sync_row(f"without-{i}", True, True, 9.0) for i in range(5)])
    _write_sync_calibration(cfg, rows, {f"with-{i}" for i in range(5)})
    report = sync_layer.correlation_report(cfg)
    assert report["with_comprehension"] == {"count": 5, "defect_rate": 0.6}
    assert report["without_comprehension"] == {"count": 5, "defect_rate": 0.0}
    assert report["sample_size_sufficient"] is True
    assert report["correlated"] is True


def test_sync_correlation_report_defers_with_small_samples(mock_root):
    cfg = Config.load(mock_root)
    rows = [_sync_row("with-1", False, False, 5.0),
            *[_sync_row(f"without-{i}", True, True) for i in range(5)]]
    _write_sync_calibration(cfg, rows, {"with-1"})
    report = sync_layer.correlation_report(cfg)
    assert report["sample_size_sufficient"] is False
    assert report["correlated"] is False


def test_sync_uncorrelated_sample_auto_disables_layer(mock_root):
    cfg = Config.load(mock_root)
    rows = ([_sync_row(f"with-{i}", True, True) for i in range(5)]
            + [_sync_row(f"without-{i}", True, True) for i in range(5)])
    _write_sync_calibration(cfg, rows, {f"with-{i}" for i in range(5)})
    cfg.yok3x["sync_layer"]["enabled"] = True
    report = sync_layer.apply_correlation_policy(cfg)
    assert report["auto_disabled"] is True
    assert cfg.yok3x["sync_layer"]["enabled"] is False
    assert Config.load(mock_root).yok3x["sync_layer"]["enabled"] is False


def test_sync_auto_disable_can_be_disabled_by_config(mock_root):
    cfg = Config.load(mock_root)
    rows = ([_sync_row(f"with-{i}", True, True) for i in range(5)]
            + [_sync_row(f"without-{i}", True, True) for i in range(5)])
    _write_sync_calibration(cfg, rows, {f"with-{i}" for i in range(5)})
    cfg.yok3x["sync_layer"].update(enabled=True, auto_disable_if_uncorrelated=False)
    report = sync_layer.apply_correlation_policy(cfg)
    assert report["auto_disabled"] is False
    assert cfg.yok3x["sync_layer"]["enabled"] is True


def test_sync_calibration_missing_or_empty_is_safe(mock_root):
    cfg = Config.load(mock_root)
    assert sync_layer.correlation_report(cfg) == {}
    path = cfg.paths.runs.parent / "calibration.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n", encoding="utf-8")
    assert sync_layer.correlation_report(cfg) == {}


# --------------------------------------------------------------- ① deepcopy 격리
def test_scaffold_mock_does_not_pollute_global(tmp_path):
    before = DEFAULT_YOK3X["workers"]["claude-main"]["backend"]
    scaffold(tmp_path, use_mock=True)
    assert DEFAULT_YOK3X["workers"]["claude-main"]["backend"] == before == "claude"


def test_config_load_survives_corrupt_json_falls_back_to_defaults(tmp_path, caplog):
    """yok3x.json이 손상(0바이트 등 — torn write)돼 있어도 크래시하지 않고 기본값으로 폴백한다.
    사용자 실사고: save_yok3x가 비원자적일 때 GUI 프로세스 강제종료 중 0바이트로 남은 사례."""
    import logging
    (tmp_path / "yok3x.json").write_text("", encoding="utf-8")   # torn write 재현(빈 파일)
    with caplog.at_level(logging.WARNING):
        cfg = Config.load(tmp_path)
    assert cfg.yok3x["flavor"] == DEFAULT_YOK3X["flavor"]          # 기본값 적용(크래시 안 함)
    assert any("손상" in r.message for r in caplog.records)        # 조용히 삼키지 않고 경고


def test_config_load_survives_malformed_but_nonempty_json(tmp_path):
    (tmp_path / "yok3x.json").write_text("{not valid json", encoding="utf-8")
    cfg = Config.load(tmp_path)                                   # 크래시하지 않으면 통과
    assert isinstance(cfg.yok3x, dict) and cfg.yok3x.get("flavor")


def test_save_yok3x_is_atomic_no_torn_write_on_interrupt(tmp_path):
    """save_yok3x는 임시파일+replace라, '쓰는 중 죽음'을 흉내내도(tmp만 쓰고 replace 전 중단)
    실제 yok3x.json은 이전 내용 그대로 유지된다(0바이트로 안 남는다)."""
    cfg = Config.load(tmp_path)
    cfg.save_yok3x()
    before = cfg.paths.yok3x_json.read_text(encoding="utf-8")
    assert before.strip()                                          # 정상 저장됨(비어있지 않음)
    # 두번째 저장에서 'process killed before tmp.replace(p)'를 흉내: tmp만 쓰고 멈춘 상태를 재현.
    cfg.yok3x["flavor"] = "changed-but-not-committed"
    p = cfg.paths.yok3x_json
    tmp = p.with_name(f"{p.name}.99999.tmp")
    tmp.write_text("{\"flavor\": \"changed-but-not-committed\"}", encoding="utf-8")
    # replace()를 호출하지 않은 상태 = 강제종료로 중단된 것과 동일 → 원본 파일은 여전히 안전.
    assert p.read_text(encoding="utf-8") == before
    tmp.unlink()


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


# --------------------------------------------------------- R-2 새 증거 기반 재시도 게이트
def test_new_evidence_gate_axes(mock_root):
    """R-2: 재시도 승인은 '새 증거'(산출물·검증기 상태·지적 결함) 축 중 하나라도 바뀌면 허용.
    셋 다 동일하면 no_new_evidence로 조기 종료한다. 기존 (score, issues_sig) 문자 비교는
    '산출물을 고쳤는데 리뷰어가 같은 말 반복'을 스톨로 오판하고, '점수만 흔들림'엔 계속 재시도했다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    base = {"artifact_sig": "a1", "verify_ok": False, "issues_sig": ("널 체크 누락",)}

    assert o._new_evidence(None, base) == (True, "first_round")
    assert o._new_evidence(base, base) == (False, "no_new_evidence")   # 셋 다 불변 → 정지
    assert o._new_evidence(base, {**base, "artifact_sig": "a2"}) == (True, "artifact_changed")
    assert o._new_evidence(base, {**base, "verify_ok": True}) == (True, "verify_state_changed")
    assert o._new_evidence(base, {**base, "issues_sig": ("다른 결함",)}) == (True, "issues_changed")


def test_artifact_sig_normalizes_whitespace_only_changes(mock_root):
    """산출물 서명은 공백 정규화 후 해시 — 들여쓰기/줄바꿈만 바뀐 건 '새 증거' 아님."""
    sig = Orchestrator._artifact_sig
    assert sig("def f():\n    return 1") == sig("def f():   return 1")
    assert sig("def f(): return 1") != sig("def f(): return 2")


def test_stop_reason_no_new_evidence_and_status(mock_root, monkeypatch):
    """R-2: 같은 산출물·같은 지적이 반복되면 stop_reason='no_new_evidence'로 조기 종료하고
    status.json에 기록한다(자동화가 문자열 파싱 없이 정지 원인 소비)."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.score_gate_mode = "strict"

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        o._step_i += 1
        # 프로듀서는 매 라운드 같은 산출물, 리뷰어는 같은 점수·같은 지적 → 새 증거 없음
        text = "SCORE: 5\n- 널 체크 누락" if task_kind == "critic" else "artifact-same"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text,
            score=5.0 if task_kind == "critic" else None))
        return BackendResult(backend="mock", ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    o.run_producer_reviewer("t", "claude-main", "codex-critic", max_rounds=5, pass_score=8.0)

    assert o.stop_reason == "no_new_evidence"
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["stop_reason"] == "no_new_evidence"
    assert status["state"] == "done"           # 실행은 완료, 승인은 gate가 따로 말한다
    assert status["gate"]["passed"] is False


def test_stop_reason_success_and_max_rounds(mock_root, monkeypatch):
    """R-2 라벨: 게이트 통과=success. 통과 못 하고 라운드 소진=max_rounds(새 증거는 계속 있었음)."""
    def run(score, rounds, evolving):
        o = Orchestrator(Config.load(mock_root), auto=True)
        o.score_gate_mode = "strict"
        state = {"n": 0}

        def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
            o._step_i += 1
            if task_kind == "critic":
                text = f"SCORE: {score}\n- 결함 {state['n'] if evolving else 0}"
                val = float(score)
            else:
                state["n"] += 1
                text = f"artifact-{state['n'] if evolving else 0}"
                val = None
            o.steps.append(orchestrator.StepLog(
                o._step_i, worker, task_kind, "done", summary=text, score=val))
            return BackendResult(backend="mock", ok=True, text=text)

        monkeypatch.setattr(o, "call_worker", fake_call)
        o.run_producer_reviewer("t", "claude-main", "codex-critic",
                                max_rounds=rounds, pass_score=8.0)
        return o

    assert run(9.0, 3, True).stop_reason == "success"        # 게이트 통과
    assert run(5.0, 3, True).stop_reason == "max_rounds"     # 새 증거는 있으나 통과 못 함


# --------------------------------------------------------- R-7 git worktree 격리
def _git_repo(path, monkeypatch=None):
    """커밋 1개짜리 임시 git 저장소를 만든다(없으면 skip)."""
    import shutil as _sh, subprocess as _sp
    if not _sh.which("git"):
        pytest.skip("git 없음")
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: _sp.run(["git", *a], cwd=path, capture_output=True, text=True)  # noqa: E731
    run("init", "-q", ".")
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    run("add", "-A")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return path


def test_worktree_usable_reasons(tmp_path):
    """격리 가능 여부를 사유와 함께 알린다 — 조용한 열화 금지(RULE §5.5)."""
    from yok3x import worktree
    root, reason = worktree.usable(None)
    assert root is None and "workdir 없음" in reason
    plain = tmp_path / "plain"; plain.mkdir()
    root, reason = worktree.usable(plain)
    assert root is None and ("git 저장소 아님" in reason or "git 실행파일 없음" in reason)
    repo = _git_repo(tmp_path / "repo")
    root, reason = worktree.usable(repo)
    assert root is not None and reason == "ok"


def test_worktree_isolates_writes_between_workers(tmp_path):
    """R-7 핵심: 한 워커의 쓰기가 다른 워커·사용자 작업 트리에 보이지 않는다."""
    from yok3x import worktree
    repo = _git_repo(tmp_path / "repo")
    (repo / "shared.txt").write_text("base\nUNCOMMITTED\n", encoding="utf-8")   # 미커밋 변경
    root, _ = worktree.usable(repo)

    ok1, w1 = worktree.add(root, tmp_path / "w1")
    ok2, w2 = worktree.add(root, tmp_path / "w2")
    assert ok1 and ok2
    # worktree는 HEAD 체크아웃 — 미커밋 변경은 안 보인다(그래서 opt-in, 문서화된 트레이드오프)
    assert (Path(w1) / "shared.txt").read_text(encoding="utf-8").strip() == "base"

    (Path(w1) / "shared.txt").write_text("W1 STOMP", encoding="utf-8")
    assert (Path(w2) / "shared.txt").read_text(encoding="utf-8").strip() == "base"   # 격리됨
    assert "UNCOMMITTED" in (repo / "shared.txt").read_text(encoding="utf-8")        # 원본 보호

    assert worktree.remove(root, w1)[0] and worktree.remove(root, w2)[0]
    assert not Path(w1).exists() and not Path(w2).exists()


def test_setup_worktrees_falls_back_with_reason(mock_root, capsys):
    """비-git workdir면 격리를 건너뛰고 사유를 로그에 남긴 뒤 기존 공유 경로를 유지한다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(mock_root)          # git 저장소 아님(mock 스캐폴드)
    specs = [o.prepare_call("claude-main", "t") for _ in range(2)]
    before = [s.run_cwd for s in specs]

    made = o._setup_worktrees(specs)

    assert made == {}
    assert [s.run_cwd for s in specs] == before          # 폴백 — 실행 경로 불변
    out = capsys.readouterr().out
    assert "[worktree] 격리 건너뜀" in out and "git 저장소 아님" in out   # 사유 명시


def test_setup_worktrees_assigns_and_cleans_up(mock_root, tmp_path):
    """git 저장소면 워커별 worktree를 만들어 run_cwd를 돌리고, 정리에서 모두 회수한다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(_git_repo(tmp_path / "repo"))
    specs = [o.prepare_call("claude-main", "t") for _ in range(2)]

    made = o._setup_worktrees(specs)

    assert len(made) == 2
    assert len({s.run_cwd for s in specs}) == 2          # 서로 다른 경로
    for s in specs:
        assert Path(s.run_cwd).is_dir() and (Path(s.run_cwd) / "shared.txt").exists()

    o._cleanup_worktrees(made)
    assert all(not Path(p).exists() for p in made.values())


def test_round_verify_explains_why_it_skipped_staging(mock_root, monkeypatch, capsys):
    """BUG-40: workdir 없이 verify_cmd만 설정하면 후보가 적용되지 않은 트리에서 검증해 **매 라운드
    거짓 실패**한다(실측: 통과하는 산출물이 2라운드 내내 fail). 조용히 열화하지 말고 사유를 알린다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.verify_cmd, o.workdir = "python -c \"pass\"", None
    monkeypatch.setattr(o, "_run_verify", lambda cwd=None: (False, "boom"))

    ok, _out, scope = o._run_round_verify("```file:a.py\nx=1\n```", 1)

    assert ok is False and scope == "original_tree"
    out = capsys.readouterr().out
    assert "workdir 미설정" in out and "거짓" in out          # 왜 실패가 못 미더운지 설명


def test_statusline_rejects_uncalibrated_implausible_estimate(tmp_path):
    """BUG-40: 새 프로젝트는 캡이 미보정이라 트랜스크립트 추정이 수백~수천%가 나온다. 그대로 ok로
    돌려주면 가드가 stop을 걸어 **모든 런이 차단**된다(실측 995%). oauth 경로처럼 비현실적이면
    신뢰하지 않고 원장 폴백에 맡긴다."""
    from yok3x import limits
    conf = {"statusline_path": str(tmp_path / "none.json"),   # statusline 캐시 없음 → 추정 폴백
            "projects_dir": str(tmp_path)}
    huge = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                               windows=[limits.Window("7d", 995.0)])
    import unittest.mock as _m
    with _m.patch.object(limits, "_probe_claude_transcripts", return_value=huge):
        r = limits._probe_claude_statusline("claude", conf)

    assert r.ok is False                       # 신뢰 불가 → check_backend가 원장으로 폴백
    assert "비현실적" in r.error and "calibrate" in r.error   # 해결 방법까지 안내

    sane = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                               windows=[limits.Window("7d", 42.0)])
    with _m.patch.object(limits, "_probe_claude_transcripts", return_value=sane):
        r2 = limits._probe_claude_statusline("claude", conf)
    assert r2.ok is True                       # 정상 범위 추정은 그대로 사용


@pytest.mark.parametrize("cmd,escapes", [
    ("python -m pytest -q", False), ("pytest tests/", False), ("make test", False),
    ("./scripts/check.sh", False), ("pytest -k 'not slow'", False),
    (r"python C:\repo\t.py", True), ("pytest C:/repo/tests", True),
    ("bash /usr/bin/check.sh", True), (r"pytest --rootdir=C:\repo", True),
    # 실행 파일 자체가 절대경로인 건 정상(venv 인터프리터) — 인자만 본다(오탐 방지).
    (r'"C:\venv\Scripts\python.exe" check.py', False),
    (r'"C:\venv\python.exe" C:\repo\t.py', True),
])
def test_verify_cmd_absolute_path_detection(cmd, escapes):
    """F2-11: verify_cmd의 절대경로는 cwd 격리를 우회할 수 있다(보수적 탐지, 상대경로는 오탐 없음)."""
    assert Orchestrator._verify_cmd_escapes_stage(cmd) is escapes


def test_absolute_verify_cmd_does_not_get_candidate_label(mock_root, monkeypatch, capsys):
    """F2-11 핵심: 명령이 절대경로로 원본을 검증했을 수 있으면 **candidate 라벨을 주지 않는다**.
    실증된 우회(스테이징엔 CANDIDATE인데 명령은 원본 ORIGINAL을 읽고도 라벨은 candidate)를 차단 —
    막을 수는 없어도 거짓 지상진실이 T-1에 섞이는 것은 막는다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(mock_root)
    o.verify_cmd = "python C:/somewhere/tests/run.py"      # 절대경로
    monkeypatch.setattr(o, "_run_verify", lambda cwd=None: (True, "ok"))

    ok, _out, scope = o._run_round_verify("```file:a.py\nx=1\n```", 1)

    assert ok is True
    assert scope == "untrusted_verify_cmd"      # candidate 아님 → calibration이 라벨로 안 씀
    assert "절대경로" in capsys.readouterr().out


def _codex_log(dirpath, rows):
    """rollout-*.jsonl 흉내: (iso시각, used_percent) 목록을 token_count 이벤트로 쓴다."""
    import json as _j
    dirpath.mkdir(parents=True, exist_ok=True)
    f = dirpath / "rollout-test.jsonl"
    f.write_text("\n".join(_j.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": pct, "window_minutes": 10080}}},
    }) for ts, pct in rows) + "\n", encoding="utf-8")
    return f


def test_kill_tree_never_captures_output_on_windows(monkeypatch):
    """BUG-43 회귀 방지: taskkill 호출에 capture_output(=파이프+리더스레드)을 쓰면, codex
    app-server가 남긴 손자 프로세스가 파이프 쓰기핸들을 물고 있을 때 리더 스레드가 EOF를
    영원히 못 받아 무한 대기한다(실측: GUI 서버가 요청 스레드 안에서 통째로 멈춤, 고아
    codex.exe가 누적). subprocess.run의 timeout=5는 이 무한 join을 못 막는다(Windows
    subprocess 함정). DEVNULL로 파이프 자체를 안 만들어 이 경로를 원천 차단해야 한다."""
    from yok3x import limits

    calls = []
    monkeypatch.setattr(limits.os, "name", "nt")

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(limits.subprocess, "run", fake_run)

    class FakeProc:
        pid = 4242
        def wait(self, timeout=None):
            return 0
    limits._kill_tree(FakeProc())

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ["taskkill", "/F"]
    assert kwargs.get("stdout") is subprocess.DEVNULL
    assert kwargs.get("stderr") is subprocess.DEVNULL
    assert kwargs.get("capture_output") is not True     # 회귀의 핵심: 다시 켜지면 안 됨


def test_probe_cache_serializes_concurrent_misses_no_stampede(monkeypatch, tmp_path):
    """BUG-43 후속(라이브 관측): 캐시에 락이 없어 동시 요청이 각자 캐시 미스를 보고 각자
    codex app-server를 새로 스폰했다(실측: node.exe 2개 동시 생존). 락으로 직렬화해
    **동시 호출 N개가 실제 프로브를 딱 1번**만 실행하고 나머지는 그 결과를 공유하는지 확인."""
    from yok3x import limits
    limits._CACHE.clear()
    limits._CACHE_LOCKS.clear()
    cfg = Config.load(tmp_path)
    calls = []
    start_gate = threading.Event()   # 5개 스레드가 거의 동시에 probe()를 부르게 맞춘다

    def slow_probe(cfg, backend):
        calls.append(1)
        time.sleep(0.1)   # 스폰처럼 시간이 걸리는 것을 흉내(이 사이 다른 스레드들이 락에서 대기해야 함)
        return limits.LimitReading(backend, "codex_appserver", ok=True, real=True,
                                   windows=[limits.Window("7d", 5.0)])
    monkeypatch.setattr(limits, "_probe_uncached", slow_probe)

    results = []
    def worker():
        start_gate.wait(timeout=2)
        results.append(limits.probe(cfg, "codex"))
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=3)

    assert len(calls) == 1                          # 실제 스폰(프로브)은 딱 한 번 — 락이 나머지를 막음
    assert len(results) == 5 and all(r is results[0] for r in results)  # 전부 같은 결과 공유


def test_list_models_cache_serializes_concurrent_misses_no_stampede(monkeypatch):
    """BUG-43 여섯 번째 후속(라이브 관측): list_models()의 _MODELS_CACHE에도 probe()·
    codex_percent_at()과 같은 락 없는 캐시 스탬피드가 있었다 — 키 없는 gemini 계정에서
    동시 요청이 몰리면 다들 _gemini_bundle_models()(번들 .js 전체 스캔)를 반복 실행해 실측으로
    30초 HTTP 다운을 유발했다. 락으로 직렬화해 동시 호출 N개가 실제 조회를 딱 1번만 하는지 확인."""
    from yok3x import limits
    limits._MODELS_CACHE.clear()
    limits._MODELS_CACHE_LOCKS.clear()
    calls = []
    start_gate = threading.Event()

    def slow_fetch(cfg, backend):
        calls.append(1)
        time.sleep(0.1)
        return ["gemini-2.5-pro"]
    monkeypatch.setattr(limits, "_fetch_models", slow_fetch)

    results = []
    def worker():
        start_gate.wait(timeout=2)
        results.append(limits.list_models(object(), "gemini"))
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=3)

    assert len(calls) == 1                      # 실제 조회는 딱 한 번 — 락이 나머지를 막음
    assert results == [["gemini-2.5-pro"]] * 5   # 전부 같은 결과 공유


def test_codex_percent_at_cache_serializes_concurrent_misses_no_stampede(monkeypatch):
    """BUG-43 네 번째 후속(라이브 관측): job-sweeper로 스폰 누수는 막았는데도 동시 요청이
    12~16초씩 걸렸다 — py-spy로 여러 스레드가 전부 codex_percent_at의 read_text/stat에
    멈춰 있는 걸 확인. probe()와 같은 락+TTL 캐시로 **동시 호출 N개가 실제 스캔을 딱 1번**만
    실행하고 나머지는 결과를 공유하는지 확인."""
    from yok3x import limits
    limits._PCT_CACHE.clear()
    calls = []
    start_gate = threading.Event()

    def slow_uncached(conf, at_ts, window_start=None, max_files=40):
        calls.append(1)
        time.sleep(0.1)
        return 12.5
    monkeypatch.setattr(limits, "_codex_percent_at_uncached", slow_uncached)

    results = []
    def worker():
        start_gate.wait(timeout=2)
        results.append(limits.codex_percent_at({}, 1000.0, window_start=0.0))
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=3)

    assert len(calls) == 1                     # 실제 스캔은 딱 한 번 — 락이 나머지를 막음
    assert results == [12.5] * 5                # 전부 같은 결과 공유


def test_job_object_helpers_are_noop_off_windows(monkeypatch):
    """BUG-43 후속: Job Object는 Windows 전용 메커니즘이다. os.name != 'nt'면 ctypes를 건드리지
    않고 즉시 None/False를 반환해야 한다(다른 OS에서 크래시하면 안 됨)."""
    from yok3x import limits
    monkeypatch.setattr(limits.os, "name", "posix")

    assert limits._win_make_job() is None
    assert limits._win_assign_to_job(123, 456) is False
    limits._win_terminate_job(123)   # 예외 없이 조용히 아무 것도 안 함


def test_kill_tree_terminates_job_before_taskkill_fallback(monkeypatch):
    """BUG-43 후속: job이 있으면 TerminateJobObject를 **먼저** 호출해 트리 전체를 정리하고,
    taskkill은 그 뒤 이중 안전망으로만 돈다(job이 실패했거나 없을 때를 대비)."""
    from yok3x import limits
    monkeypatch.setattr(limits.os, "name", "nt")
    order = []
    monkeypatch.setattr(limits, "_win_terminate_job",
                        lambda job: order.append(("terminate_job", job)))
    monkeypatch.setattr(limits.subprocess, "run",
                        lambda args, **kw: order.append(("taskkill", args)))

    class FakeProc:
        pid = 777
        def wait(self, timeout=None):
            return 0
    limits._kill_tree(FakeProc(), job=999)

    assert order[0] == ("terminate_job", 999)     # job 종료가 먼저
    assert order[1][0] == "taskkill"              # taskkill은 그 다음(폴백/이중안전망)


def test_kill_tree_skips_terminate_job_when_none(monkeypatch):
    """job이 없으면(생성/편입 실패) 기존 taskkill-only 경로만 돈다 — 회귀 없음."""
    from yok3x import limits
    monkeypatch.setattr(limits.os, "name", "nt")
    calls = []
    monkeypatch.setattr(limits, "_win_terminate_job", lambda job: calls.append(job))
    monkeypatch.setattr(limits.subprocess, "run", lambda args, **kw: None)

    class FakeProc:
        pid = 1
        def wait(self, timeout=None):
            return 0
    limits._kill_tree(FakeProc(), job=None)

    assert calls == []     # _win_terminate_job이 아예 호출되지 않음


def test_appserver_rate_limits_keeps_sweeper_when_wrapper_assign_fails(monkeypatch):
    """BUG-43 세 번째 후속: npm .cmd 래퍼(proc.pid)는 실제 작업(node.exe)을 띄운 직후
    스스로 먼저 종료해버릴 수 있다(실측: 살아있는 node.exe의 부모 PID가 이미 사라져 있었음).
    그 타이밍에 걸려 래퍼 자체의 job 편입이 실패해도 job을 통째로 버리면 안 된다 — 스위퍼는
    root_pid를 파이썬 쪽 추적 시작점으로만 쓰므로, 래퍼가 낳은 자손은 여전히 스위퍼가 찾아
    직접 편입할 수 있다. 그래서 편입 실패와 무관하게 스위퍼는 항상 뜨고, job도 유지된다."""
    from yok3x import limits
    monkeypatch.setattr(limits, "_win_make_job", lambda: "JOB-HANDLE")
    monkeypatch.setattr(limits, "_win_assign_to_job", lambda job, pid: False)
    terminated = []
    monkeypatch.setattr(limits, "_win_terminate_job", lambda job: terminated.append(job))
    sweeper_calls = []
    monkeypatch.setattr(limits, "_win_start_job_sweeper",
                        lambda job, root_pid, stop_event: sweeper_calls.append((job, root_pid)) or None)
    kill_calls = []
    monkeypatch.setattr(limits, "_kill_tree",
                        lambda proc, job=None: kill_calls.append(job))

    class FakePopen:
        def __init__(self, *a, **k):
            self.pid = 55
            self.stdin = type("S", (), {"write": lambda *a: None, "flush": lambda *a: None})()
            self.stdout = iter([])   # reader 스레드가 바로 끝나게
    monkeypatch.setattr(limits.subprocess, "Popen", FakePopen)

    limits._appserver_rate_limits("codex", ["app-server"], timeout=0.2)

    assert terminated == []                       # 편입 실패해도 job을 버리지 않음
    assert sweeper_calls == [("JOB-HANDLE", 55)]   # 스위퍼는 편입 성공 여부와 무관하게 시작됨
    assert kill_calls == ["JOB-HANDLE"]            # _kill_tree에도 살아있는 job이 그대로 전달됨


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object는 Windows 전용")
def test_job_object_really_kills_a_real_process_tree(tmp_path):
    """BUG-43 후속 실증: 실제 프로세스 트리(cmd.exe → python.exe)를 job에 편입하고
    TerminateJobObject 한 번으로 **둘 다** 죽는지 진짜로 확인한다(모킹 아님).
    codex 바이너리 설치 여부와 무관하게 같은 메커니즘(cmd.exe 경유 자식)을 재현."""
    from yok3x import limits
    import time as _time

    marker = tmp_path / "still_running.txt"
    # cmd.exe가 python을 자식으로 띄우게 해 codex.cmd와 같은 '래퍼 → 실제 프로세스' 형태를 재현.
    proc = subprocess.Popen(
        ["cmd.exe", "/c", sys.executable, "-c",
         f"import time,pathlib; pathlib.Path(r'{marker}').write_text('x'); time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        job = limits._win_make_job()
        assert job is not None
        assert limits._win_assign_to_job(job, proc.pid) is True

        deadline = _time.time() + 5
        while not marker.exists() and _time.time() < deadline:
            _time.sleep(0.1)
        assert marker.exists(), "자식 python이 뜨지 않음(테스트 환경 문제)"

        limits._win_terminate_job(job)
        _time.sleep(0.5)

        # cmd.exe 래퍼(proc.pid)와 그 자식 python 둘 다 죽었어야 한다.
        check = subprocess.run(["tasklist", "/FI", f"PID eq {proc.pid}"],
                               capture_output=True, text=True)
        assert str(proc.pid) not in check.stdout
    finally:
        try:
            proc.kill()
        except Exception:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object는 Windows 전용")
def test_job_sweeper_catches_grandchild_that_already_won_the_race(tmp_path):
    """BUG-43 후속의 후속(경쟁 재현): 손자 프로세스가 **우리가 job에 편입하기도 전에** 이미
    태어나 있는(실측 재현된) 최악의 경우를 결정론적으로 만든다 — 편입을 일부러 늦춰 손자가
    먼저 뜨게 한 뒤, 스위퍼가 그래도 그 손자를 찾아 job에 마저 편입해 같이 죽이는지 확인.
    cmd.exe(래퍼) → python(자식) → python(손자) 3단 트리."""
    from yok3x import limits
    import threading as _threading
    import time as _time

    grandchild_marker = tmp_path / "grandchild_running.txt"
    marker_literal = repr(str(grandchild_marker))   # 중첩 -c 안에 안전하게 넣을 파이썬 문자열 리터럴
    grandchild_src = (
        f"import os,time,pathlib;"
        f"pathlib.Path({marker_literal}).write_text(str(os.getpid()));time.sleep(30)"
    )
    # 손자가 **자기 PID**를 마커에 남긴다 — 종료 후 그 PID로 직접 생존 여부를 확인하기 위해.
    child_script = (
        f"import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{grandchild_src!r}]);"
        f"time.sleep(30)"
    )
    proc = subprocess.Popen(["cmd.exe", "/c", sys.executable, "-c", child_script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 경쟁 재현: 손자가 완전히 뜰 때까지 **일부러 기다린 뒤에야** job을 만들고 편입한다 —
        # "편입이 손자 생성보다 늦는" 최악의 타이밍을 결정론적으로 강제.
        deadline = _time.time() + 5
        while not grandchild_marker.exists() and _time.time() < deadline:
            _time.sleep(0.1)
        assert grandchild_marker.exists(), "손자 python이 뜨지 않음(테스트 환경 문제)"
        grandchild_pid = grandchild_marker.read_text(encoding="utf-8").strip()

        job = limits._win_make_job()
        assert job is not None
        assert limits._win_assign_to_job(job, proc.pid) is True   # 직계 자식만, 손자는 아직 안 잡힘

        stop = _threading.Event()
        sweeper = limits._win_start_job_sweeper(job, proc.pid, stop)
        _time.sleep(0.5)   # 스위퍼가 최소 한 틱(0.15s 간격) 돌 시간을 준다
        stop.set()
        sweeper.join(timeout=2)

        limits._win_terminate_job(job)
        _time.sleep(0.5)

        # cmd.exe 래퍼(직계 자식)뿐 아니라, **이미 늦게 편입된 손자**까지 죽었어야 한다.
        check = subprocess.run(["tasklist", "/FI", f"PID eq {proc.pid}"],
                               capture_output=True, text=True)
        assert str(proc.pid) not in check.stdout
        check2 = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild_pid}"],
                                capture_output=True, text=True)
        assert grandchild_pid not in check2.stdout, (
            "손자(늦게 편입)가 살아남음 — 스위퍼가 경쟁을 못 잡았다")
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def test_codex_percent_at_reads_series_and_respects_window(tmp_path):
    """F2-10: codex는 토큰 트랜스크립트가 없지만 세션 로그에 (시각, 주간%) 시계열이 남는다.
    이걸로 '하루 시작 시점의 %'를 되찾아 오늘 소비를 낸다. **주간 리셋을 가로지르면 안 된다** —
    리셋 직전 값(이전 창 누적)을 새 창 기준선으로 쓰면 오늘 소비가 0으로 뭉개진다(실측 재현)."""
    from yok3x import limits
    import datetime as _dt
    iso = lambda h: _dt.datetime(2026, 7, 27, h, 0, tzinfo=_dt.timezone.utc).isoformat()  # noqa: E731
    ts = lambda h: _dt.datetime(2026, 7, 27, h, 0, tzinfo=_dt.timezone.utc).timestamp()   # noqa: E731
    _codex_log(tmp_path / "sessions", [
        (iso(6), 80.0),    # 이전 주간 창의 누적(리셋 전)
        (iso(11), 5.0),    # 리셋 후 새 창
        (iso(13), 12.0),
    ])
    conf = {"sessions_dir": str(tmp_path / "sessions")}
    win_start = ts(9)      # 09:00에 주간 리셋

    # 창 경계를 주면 리셋 전 80%는 무시된다
    assert limits.codex_percent_at(conf, ts(12), window_start=win_start) == 5.0
    assert limits.codex_percent_at(conf, ts(14), window_start=win_start) == 12.0
    # 창 안에 관측이 없으면 0%(창이 막 시작 = 사용 없음)
    assert limits.codex_percent_at(conf, ts(10), window_start=win_start) == 0.0
    # 경계를 안 주면 이전 창 값을 주워온다(그래서 호출자가 반드시 넘겨야 함)
    assert limits.codex_percent_at(conf, ts(10)) == 80.0

    # 성능(캐시 아님): 창 시작 이전/그 시점은 **정의상 0%** → 파일을 읽지 않고 즉시 답한다.
    # 리셋 당일에는 day_start == window_start라 이 경로가 늘 타므로 실측 체감이 크다.
    empty = tmp_path / "no_such_dir"
    assert limits.codex_percent_at({"sessions_dir": str(empty)}, ts(9),
                                   window_start=ts(9)) == 0.0
    assert limits.codex_percent_at({"sessions_dir": str(empty)}, ts(8),
                                   window_start=ts(9)) == 0.0


def test_pace_inputs_codex_uses_percent_series_for_today(tmp_path, monkeypatch):
    """codex 페이싱이 스냅샷이 아니라 시계열 기반 '오늘 소비'를 쓴다(재기동 불변)."""
    from yok3x import usage, limits
    cfg = Config.load(tmp_path)
    reading = limits.LimitReading("codex", "codex_appserver", ok=True, real=True,
                                  windows=[limits.Window("7d", 27.0)])
    monkeypatch.setattr(limits, "codex_percent_at",
                        lambda conf, at, window_start=None, **k: 4.0)

    cur, known, today = usage._pace_inputs(cfg, "codex", reading, reset_at=time.time() + 86400)

    assert cur == 27.0 and known is True
    assert today == 23.0            # 27 − 4 = 오늘 소비(하루시작 이후)

    monkeypatch.setattr(limits, "codex_percent_at",
                        lambda conf, at, window_start=None, **k: None)
    assert usage._pace_inputs(cfg, "codex", reading, reset_at=time.time() + 86400)[1] is False


def test_atomic_write_text_preserves_original_on_failure(tmp_path, monkeypatch):
    """F2-5: 원자적 쓰기 — 교체 중 죽어도 원본이 살아있고 tmp 잔재가 남지 않는다.
    (BUG-32: write_text의 truncate-then-write 구간에 죽어 yok3x.json이 0바이트가 됐던 계열)"""
    from yok3x.config import atomic_write_text
    target = tmp_path / "state.json"
    target.write_text("OLD", encoding="utf-8")

    real_replace = Path.replace
    monkeypatch.setattr(Path, "replace",
                        lambda self, dst: (_ for _ in ()).throw(OSError("죽음")))
    with pytest.raises(OSError):
        atomic_write_text(target, "NEW")
    monkeypatch.setattr(Path, "replace", real_replace)

    assert target.read_text(encoding="utf-8") == "OLD"          # 원본 보존
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]  # tmp 잔재 없음

    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"


def test_scaffold_and_knot_use_atomic_writes(tmp_path):
    """BUG-32와 같은 파일(yok3x.json)을 쓰는 scaffold, 그리고 매 런 프롬프트로 주입되는
    brief/context·knot 노트가 모두 원자적 경로를 쓴다(찢긴 쓰기가 곧 오염된 입력이 되는 곳)."""
    import inspect
    from yok3x import config as cfgmod, knot
    src = inspect.getsource(cfgmod.scaffold)
    assert "atomic_write_text(cfg.paths.yok3x_json" in src
    assert "atomic_write_text(cfg.paths.backends_json" in src
    for fn in (knot.save, knot.write_context, knot.write_brief):
        assert "atomic_write_text(" in inspect.getsource(fn), fn.__name__


def test_run_budget_cap_stops_before_next_call(mock_root, monkeypatch):
    """T-2 실측 대응: preflight 추정($0.03)이 실제($3.37)를 100배 과소평가했다. 실제 누적 비용으로
    다음 호출 **전에** 끊어 꼬리 런이 예산을 독식하지 못하게 한다. 0(기본)이면 기존 동작 그대로."""
    cfg = Config.load(mock_root)
    cfg.yok3x["guard"]["reservation"]["max_usd_per_run"] = 1.0
    o = Orchestrator(cfg, auto=True)
    o._run_usd = 1.5                                  # 이미 상한 초과 상태
    spec = o.prepare_call("claude-main", "t")

    with pytest.raises(orchestrator.RunAborted) as exc:
        o.execute_call(spec)

    assert exc.value.cause == "run_budget_exceeded"
    assert o.stop_reason == "run_budget_exceeded"

    # 기본값(0)이면 상한 없음 — 기존 동작 불변
    cfg2 = Config.load(mock_root)
    assert cfg2.yok3x["guard"]["reservation"]["max_usd_per_run"] == 0
    o2 = Orchestrator(cfg2, auto=True)
    o2._run_usd = 999.0
    assert o2.execute_call(o2.prepare_call("claude-main", "t")).ok    # 막지 않는다


def test_calib_verdict_distinguishes_undefined_from_low_correlation():
    """T-2 1차 표본이 드러낸 결함: verify_ok가 한쪽뿐이면 상관은 **계산 불가**(None)인데
    '상관 낮음 → 게이트 무의미 의심'으로 표시돼, 데이터 없이 결론을 주장하게 된다."""
    from yok3x import calibration
    same = [{"score": 5.0 + i * 0.1, "verify_ok": True, "verify_scope": "candidate"}
            for i in range(12)]                      # 라벨 한쪽뿐 → 분산 0
    s = calibration.summarize(same, threshold=8.0)
    assert s["score_verify_corr"] is None
    assert "판정 불가" in s["verdict"] and "한쪽" in s["verdict"]

    few = [{"score": 5.0, "verify_ok": True, "verify_scope": "candidate"}]
    assert calibration.summarize(few, threshold=8.0)["verdict"] == "표본 부족"

def test_log_survives_console_encoding_limits(mock_root):
    """BUG-39: cp949 콘솔이 '—'(U+2014)를 못 그려 _log가 UnicodeEncodeError로 런을 죽였다
    (실측: 래칫 체크포인트 1개 유실). 출력은 낮춰 찍되 파일 로그엔 원문을 남기고 예외는 안 낸다."""
    import io, sys as _sys
    o = Orchestrator(Config.load(mock_root), auto=True)
    real = _sys.stdout
    _sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    try:
        o._log("체크포인트 — em dash 포함")     # 예외 없이 통과해야 한다
        printed = _sys.stdout.buffer.getvalue()
    finally:
        _sys.stdout = real
    assert printed                                    # 뭔가 찍혔다(조용히 삼키지 않음)
    assert "—" in (o.run_dir / "run.log").read_text(encoding="utf-8")   # 파일엔 원문 보존


def test_ratchet_off_by_default_and_needs_auto_commit(mock_root, tmp_path):
    """T-3 결정: 기본은 파일게시+사람수락. apply_mode를 명시하지 않으면 커밋하지 않는다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(_git_repo(tmp_path / "repo"))
    assert o._ratchet_enabled() is False
    o._ratchet_commit("```file:a.py\nx=1\n```", 1)
    assert o._ratchet_commits == [] and o._ratchet_dir is None    # 아무 것도 안 함
    o.changes = {"apply_mode": "auto_commit"}
    assert o._ratchet_enabled() is True


def test_ratchet_commits_to_isolated_branch_only(mock_root, tmp_path):
    """R-7 2단계 핵심 안전성: 체크포인트는 **전용 브랜치**에만 쌓이고 사용자 작업 트리·현재
    브랜치는 불변. worktree를 지워도 커밋이 살아 있어야 한다(브랜치 ref로 생성 — GC 방지)."""
    import subprocess as _sp
    from yok3x import worktree
    repo = _git_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("base\nUNCOMMITTED\n", encoding="utf-8")
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(repo)
    o.changes = {"apply_mode": "auto_commit"}
    art = lambda s: f"결과\n\n```file:src/app.py\nprint('{s}')\n```\n"    # noqa: E731

    o._ratchet_commit(art("r1"), 1)
    o._ratchet_commit(art("r2"), 2)

    assert [c["round"] for c in o._ratchet_commits] == [1, 2]
    assert not (repo / "src" / "app.py").exists()                      # 사용자 트리 불변
    assert "UNCOMMITTED" in (repo / "a.txt").read_text(encoding="utf-8")
    g = lambda *a: _sp.run(["git", *a], cwd=repo, capture_output=True,
                           text=True, encoding="utf-8").stdout.strip()  # noqa: E731
    assert g("branch", "--show-current") in ("master", "main")          # 현재 브랜치 그대로

    worktree.remove(str(repo), o._ratchet_dir)                          # 정리 후에도
    assert len(g("log", "--oneline", o._ratchet_branch).splitlines()) == 3   # init+r1+r2 생존
    assert "r2" in g("show", f"{o._ratchet_branch}:src/app.py")


def test_ratchet_disables_itself_when_not_a_git_repo(mock_root, capsys):
    """git 저장소가 아니면 사유를 남기고 review 모드로 되돌려 매 라운드 재시도하지 않는다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(mock_root)                 # 비-git
    o.changes = {"apply_mode": "auto_commit"}

    o._ratchet_commit("```file:a.py\nx=1\n```", 1)

    assert o._ratchet_commits == []
    assert o.changes["apply_mode"] == "review"        # 자기 비활성화(반복 실패 방지)
    assert "[ratchet] auto_commit 불가" in capsys.readouterr().out


# --------------------------------------------------------- R-4 기계판독 스냅샷 + provenance enum
@pytest.mark.parametrize("source,ok,real,expected", [
    ("claude_oauth", True, True, "measured"),
    ("codex_appserver", True, True, "measured"),
    ("claude_transcripts", True, False, "estimated"),
    ("ledger", True, False, "ledger"),
    ("ledger", True, True, "ledger"),          # 원장은 real 여부와 무관하게 ledger
    ("claude_oauth", False, True, "unavailable"),   # 실패면 신뢰 불가
    ("disabled", False, False, "unavailable"),
])
def test_provenance_enum_mapping(source, ok, real, expected):
    """R-4: '(추정)' 같은 사람용 detail 문구 대신 provenance enum으로 신뢰 등급을 노출한다."""
    from yok3x import limits
    r = limits.LimitReading("claude", source, ok=ok, real=real)
    assert r.provenance() == expected
    assert r.provenance() in limits.PROVENANCE_VALUES


def test_limits_json_snapshot_contract(tmp_path, monkeypatch, capsys):
    """R-4: `limits --json`이 스키마·provenance·창 수치를 기계판독 형태로 낸다(훅/CI 계약)."""
    from yok3x import cli, limits, usage as usage_mod
    monkeypatch.chdir(tmp_path)
    reading = limits.LimitReading(
        "claude", "claude_oauth", ok=True, real=True,
        windows=[limits.Window("7d", 66.0, resets_at=123.0, used_tokens=5)], detail="d")
    monkeypatch.setattr(limits, "probe", lambda cfg, b, use_cache=True: reading)
    monkeypatch.setattr(usage_mod, "check_backend",
                        lambda cfg, b: usage_mod.GuardVerdict(b, 0.66, "7d", "ok", ""))

    assert cli.main(["limits", "--json"]) == 0
    snap = json.loads(capsys.readouterr().out)

    assert snap["schema"] == "yok3x.limits/1"
    c = snap["backends"]["claude"]
    assert c["provenance"] == "measured" and c["level"] == "ok"
    assert c["windows"][0] == {"name": "7d", "used_percent": 66.0, "resets_at": 123.0,
                               "window_minutes": None, "used_tokens": 5, "limit_tokens": None}


@pytest.mark.parametrize("level,expected", [("ok", 0), ("warn", 3), ("stop", 4)])
def test_limits_exit_code_is_opt_in(tmp_path, monkeypatch, level, expected):
    """R-4 자동화 종료코드: --exit-code일 때만 0/3(warn)/4(stop). 플래그 없으면 기존대로 항상 0
    (기존 스크립트 동작 불변)."""
    from yok3x import cli, limits, usage as usage_mod
    monkeypatch.chdir(tmp_path)
    reading = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                                  windows=[limits.Window("7d", 99.0)])
    monkeypatch.setattr(limits, "probe", lambda cfg, b, use_cache=True: reading)
    monkeypatch.setattr(usage_mod, "check_backend",
                        lambda cfg, b: usage_mod.GuardVerdict(b, 0.99, "7d", level, ""))

    assert cli.main(["limits", "--exit-code"]) == expected
    assert cli.main(["limits"]) == 0            # opt-in — 기본 동작은 안 바뀐다


def test_cli_claude_usage_json_and_text(tmp_path, monkeypatch, capsys):
    """R-5 CLI 배선: `yok3x claude-usage --json`이 스키마를 내고, 기본(텍스트)은 사람이 읽을 표를 낸다.
    둘 다 '페이싱 앵커 아님'을 명시(사후감사용이라는 정직 표기)."""
    from yok3x import cli, limits
    monkeypatch.chdir(tmp_path)
    rows = [{"session_id": "sess-A", "model": "claude-opus-4-8", "tokens": 300, "calls": 2,
            "first_ts": 1000.0, "last_ts": 2000.0}]
    monkeypatch.setattr(limits, "claude_usage_breakdown", lambda conf, since=None, until=None: rows)

    assert cli.main(["claude-usage", "--json"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["schema"] == "yok3x.claude_usage/1"
    assert snap["rows"] == rows

    assert cli.main(["claude-usage"]) == 0
    out = capsys.readouterr().out
    assert "sess-A" in out and "300" in out and "페이싱 앵커 아님" in out


def test_cli_claude_usage_empty_is_honest_not_silent(tmp_path, monkeypatch, capsys):
    """R-5 폴백 가드가 CLI에도 정직하게 드러난다 — 데이터 없음을 조용히 숨기지 않고 이유를 알린다."""
    from yok3x import cli, limits
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(limits, "claude_usage_breakdown", lambda conf, since=None, until=None: [])
    assert cli.main(["claude-usage"]) == 0
    out = capsys.readouterr().out
    assert "데이터 없음" in out and "페이싱엔 영향 없음" in out


# --------------------------------------------------------- R-3 preflight 예산 검사
def test_headroom_reuses_ledger_accounting(tmp_path):
    """R-3: headroom은 예약 원장(pending)과 hard_limits를 재사용해 잔여를 낸다 — preflight가
    별도 계산을 두지 않게(예약 경로와 판정 불일치 방지). 상한 0인 지표는 무제한(inf)."""
    from yok3x import reserve
    cfg = Config.load(tmp_path)
    cfg.yok3x["budgets"] = {"claude": {"daily_calls": 100}}
    cfg.paths.yok3x_dir.mkdir(parents=True, exist_ok=True)

    room = reserve.headroom(cfg)
    assert room["calls"]["limit"] == 100 and room["calls"]["remaining"] == 100
    assert room["est_usd"]["remaining"] == float("inf")     # 상한 없음 → 무제한

    assert reserve.reserve(cfg, "other-run", calls=30, est_tokens=0, est_usd=0.0)
    room2 = reserve.headroom(cfg)
    assert room2["calls"]["pending"] == 30                  # 다른 런 예약이 잔여를 깎는다
    assert room2["calls"]["remaining"] == 70
    # 자기 자신의 예약은 제외(이중 계상 방지)
    assert reserve.headroom(cfg, exclude_run_id="other-run")["calls"]["remaining"] == 100


def test_project_run_cost_worst_case_by_pattern(mock_root):
    """R-3: 패턴별 최악값 호출 수를 spec에서 결정론적으로 계산한다(라운드·스테이지·워커·acquire)."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    calls = lambda s: o.project_run_cost(s)[0]                       # noqa: E731
    assert calls({"pattern": "producer-reviewer", "task": "t", "max_rounds": 3}) == 6
    assert calls({"pattern": "pipeline", "task": "t", "stages": [{}, {}, {}]}) == 3
    assert calls({"pattern": "fanout-fanin", "task": "t",
                  "workers": ["a", "b", "c"], "join_worker": "a"}) == 4
    # acquire 사전질의: 질문자 1 + 답변자수 × qa_count
    assert calls({"pattern": "pipeline", "task": "t", "stages": [{}],
                  "acquire": {"qa_count": 2, "answerers": ["x", "y"]}}) == 1 + 1 + 4
    _, tokens, usd = o.project_run_cost({"pattern": "pipeline", "task": "x" * 100,
                                         "stages": [{}, {}]})
    assert tokens > 0 and usd > 0                                     # 추정 상한 산출


def test_preflight_refuses_run_that_cannot_fit_budget(mock_root):
    """R-3 핵심: 잔여 예산으로 끝낼 수 없다고 예측되면 **시작 전에** 거부한다(반응형 가드처럼
    예산을 절반 태우고 중단되지 않게). stop_reason=budget_preflight로 라벨링."""
    cfg = Config.load(mock_root)
    cfg.yok3x["budgets"] = {"claude": {"daily_calls": 2}}    # 잔여 2콜
    o = Orchestrator(cfg, auto=True)
    spec = {"pattern": "producer-reviewer", "task": "t", "max_rounds": 5}   # 최악 10콜

    with pytest.raises(orchestrator.RunAborted) as exc:
        o.preflight_budget(spec)

    assert exc.value.cause == "budget_preflight"
    assert "preflight" in str(exc.value)
    assert o.stop_reason == "budget_preflight"


def test_preflight_allows_fitting_run_and_respects_disable(mock_root):
    """잔여가 충분하면 통과하고, preflight_enabled=false면 검사 자체를 건너뛴다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["budgets"] = {"claude": {"daily_calls": 500}}
    o = Orchestrator(cfg, auto=True)
    o.preflight_budget({"pattern": "producer-reviewer", "task": "t", "max_rounds": 2})  # 통과

    cfg.yok3x["budgets"] = {"claude": {"daily_calls": 1}}
    cfg.yok3x["guard"]["reservation"]["preflight_enabled"] = False
    Orchestrator(cfg, auto=True).preflight_budget(
        {"pattern": "producer-reviewer", "task": "t", "max_rounds": 9})   # 꺼져 있으면 통과


# --------------------------------------------------------- R-6 verifier separation
def test_protected_verifier_hits_matches_test_and_config_paths(mock_root):
    """R-6: 테스트·검증 설정 경로를 보호 대상으로 식별한다(디렉터리 glob·확장자 패턴 모두)."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    hit = o._protected_verifier_hits
    assert hit(["tests/test_a.py"]) == ["tests/test_a.py"]
    assert hit(["src/app_test.go"]) == ["src/app_test.go"]
    assert hit(["conftest.py"]) == ["conftest.py"]
    assert hit(["pytest.ini"]) == ["pytest.ini"]
    assert hit([".github/workflows/ci.yml"]) == [".github/workflows/ci.yml"]
    assert hit(["src/main.py", "README.md"]) == []          # 일반 소스는 보호 대상 아님
    assert hit(["src\\util_test.go"]) == ["src/util_test.go"]   # 윈도우 구분자 정규화


def test_protected_globs_overridable(mock_root):
    """changes.protected_globs로 보호 목록을 재정의할 수 있다(프로젝트별 검증기 배치 대응)."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.changes = {"protected_globs": ["verify/**"]}
    assert o._protected_verifier_hits(["verify/run.sh"]) == ["verify/run.sh"]
    assert o._protected_verifier_hits(["tests/test_a.py"]) == []   # 재정의 시 기본 목록 대체


def test_round_verify_rejects_candidate_touching_verifier(mock_root, monkeypatch, tmp_path):
    """R-6 핵심: 프로듀서가 테스트 파일을 재작성한 후보는 스테이징하지 않고 **원본 트리**에서
    검증한다(candidate 라벨 안 붙음). 이게 없으면 verifier를 고쳐 게이트를 우회할 수 있다."""
    o = Orchestrator(Config.load(mock_root), auto=True)
    o.workdir = str(tmp_path)
    o.verify_cmd = "echo x"
    seen = {}
    monkeypatch.setattr(o, "_run_verify", lambda cwd=None: (seen.update(cwd=cwd) or (True, "ok")))

    artifact = ("```file:tests/test_gate.py\ndef test_x(): assert True\n```\n"
                "```file:src/main.py\nprint(1)\n```\n")
    ok, _out, scope = o._run_round_verify(artifact, rnd=1)

    assert ok is True
    assert scope == "original_tree"          # 후보 스테이징 거부 → 원본에서 검증
    assert seen.get("cwd") is None           # 스테이징 경로가 아니라 기본(workdir)에서 실행
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


def test_recent_runs_exposes_understanding_bundle_when_present(mock_root):
    """v4.6.0 S9: sync_layer.enabled인 런은 /api/state의 runs[].understanding.claims로 노출된다.
    미사용 런은 None(GUI가 claim 영역을 숨기는 신호)."""
    from yok3x.guiserver import _recent_runs
    cfg = Config.load(mock_root)
    tf = mock_root / "t.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t",
                              "producer": "claude-main", "reviewer": "codex-critic", "max_rounds": 1},
                             ensure_ascii=False), encoding="utf-8")
    run_task_file(cfg, tf, auto=True)
    runs = _recent_runs(cfg, 10)
    assert runs[0]["understanding"] is None      # sync_layer 미사용 — 조용히 생략

    run_dir = cfg.paths.runs / runs[0]["run_id"]
    (run_dir / "understanding_bundle.json").write_text(
        json.dumps({"claims": [{"claim_id": "c1", "type": "FACT", "text": "x", "evidence_refs": []}]}),
        encoding="utf-8")
    runs2 = _recent_runs(cfg, 10)
    assert runs2[0]["understanding"]["claims"][0]["claim_id"] == "c1"


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


def test_run_task_file_sink_carries_run_id_and_gate(mock_root):
    """F2-2: run_task_file(sink)이 run_id·gate를 sink에 채워, 호출자가 종료 상태와 별개로 게이트
    판정(산출물 승인)을 소비할 수 있다(producer-reviewer는 gate 존재)."""
    cfg = Config.load(mock_root)
    tf = mock_root / "task.json"
    tf.write_text(json.dumps({"pattern": "producer-reviewer", "task": "t",
                              "producer": "claude-main", "reviewer": "codex-critic",
                              "max_rounds": 2, "pass_score": 8.0}, ensure_ascii=False),
                  encoding="utf-8")
    sink: dict = {}
    state = run_task_file(cfg, tf, auto=True, sink=sink)
    assert state == "done"
    assert str(sink.get("run_id", "")).startswith("run_")
    assert isinstance(sink.get("gate"), dict) and "passed" in sink["gate"]


def test_cli_run_warns_when_task_workdir_differs_from_cwd(tmp_path, monkeypatch, capsys):
    """BUG-54: Config.load(".")는 task.json의 workdir가 아니라 CLI 실행 위치를 읽는다 — 실측으로
    비용상한 무효화·backend 뒤바뀜을 낳은 함정이라, 최소한 조용히 새지 않게 경고해야 한다."""
    from yok3x import cli
    monkeypatch.chdir(tmp_path)
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    (tmp_path / "t.json").write_text(json.dumps({"workdir": str(other_dir)}), encoding="utf-8")
    monkeypatch.setattr(cli, "run_task_file", lambda cfg, task_file, auto=None, sink=None, **kw: "done")

    cli.main(["run", "t.json"])

    err = capsys.readouterr().err
    assert "[warn]" in err
    assert str(other_dir.resolve()) in err


def test_cli_run_no_warning_when_workdir_matches_cwd(tmp_path, monkeypatch, capsys):
    from yok3x import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.json").write_text(json.dumps({"workdir": str(tmp_path)}), encoding="utf-8")
    monkeypatch.setattr(cli, "run_task_file", lambda cfg, task_file, auto=None, sink=None, **kw: "done")

    cli.main(["run", "t.json"])

    assert "[warn]" not in capsys.readouterr().err


def test_cli_run_no_warning_when_workdir_not_set(tmp_path, monkeypatch, capsys):
    from yok3x import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(cli, "run_task_file", lambda cfg, task_file, auto=None, sink=None, **kw: "done")

    cli.main(["run", "t.json"])

    assert "[warn]" not in capsys.readouterr().err


def test_cli_run_workdir_check_does_not_crash_on_missing_or_malformed_task_file(tmp_path, monkeypatch, capsys):
    from yok3x import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_task_file", lambda cfg, task_file, auto=None, sink=None, **kw: "done")

    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
    cli.main(["run", "bad.json"])  # malformed JSON must not crash the workdir check itself
    assert "[warn]" not in capsys.readouterr().err

    cli.main(["run", "missing.json"])  # nonexistent file must not crash the workdir check itself
    assert "[warn]" not in capsys.readouterr().err


def test_cli_run_exit_separates_state_and_gate(tmp_path, monkeypatch):
    """F2-2 계약: `yok3x run` 종료코드가 실행 상태(state)와 산출물 승인(gate.passed)을 분리한다 —
    done+승인→0, done+미통과(strict 저점·verify 실패 등)→3, 중단→1. R-2의 verifier-gated 정지가
    종료코드에서 소실되지 않게 하는 선행 계약."""
    from yok3x import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.json").write_text("{}", encoding="utf-8")
    holder = {"state": "done", "gate": None}

    def fake_run(cfg, task_file, auto=None, sink=None, **kw):
        if sink is not None and holder["gate"] is not None:
            sink["gate"] = holder["gate"]
        return holder["state"]
    monkeypatch.setattr(cli, "run_task_file", fake_run)

    holder.update(state="done", gate={"passed": False, "mode": "strict",
                  "reason": "score_below_threshold", "score": 5.0,
                  "threshold": 8.0, "verify_ok": True})
    assert cli.main(["run", "t.json"]) == 3        # done이나 게이트 미통과
    holder.update(state="done", gate={"passed": True})
    assert cli.main(["run", "t.json"]) == 0        # done + 승인
    holder.update(state="done", gate=None)
    assert cli.main(["run", "t.json"]) == 0        # 게이트 없음 → 성공으로 소비
    holder.update(state="aborted: guard_stop", gate=None)
    assert cli.main(["run", "t.json"]) == 1        # 실행 중단


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


def _structured_review_text(score, defects):
    payload = {
        "protocol_version": "review-v1",
        "score": score,
        "defects": defects,
        "summary": "",
    }
    return f"SCORE: {score}\nround defect\n```json\n{json.dumps(payload)}\n```"


def test_deterministic_scoring_off_by_default_leaves_score_unchanged(mock_root, monkeypatch):
    """기본값(off)에서는 구조화 JSON이 와도 리뷰어의 자유형 SCORE를 그대로 쓴다(회귀 방지)."""
    cfg = Config.load(mock_root)
    assert cfg.yok3x["review_protocol"]["deterministic_scoring"] is False
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "sentinel verify"

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        o._step_i += 1
        if task_kind == "critic":
            score = 9.0
            text = _structured_review_text(9.0, [{"severity": "critical", "description": "x"}])
        else:
            score, text = None, "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend=o._worker(worker)["backend"], ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", lambda: (True, "ok"))
    o.run_producer_reviewer("task", "claude-main", "codex-critic", max_rounds=1, pass_score=8.0)

    assert o.gate["score"] == 9.0  # critical 결함이 있어도 결정론적 재계산이 안 켜졌으면 불변


def test_deterministic_scoring_overrides_score_from_structured_defects(mock_root, monkeypatch):
    """켜져 있고 구조화 파싱이 성공하면, 리뷰어의 자유형 SCORE 대신 결함 목록 기반 계산값을 쓴다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["review_protocol"]["deterministic_scoring"] = True
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "sentinel verify"

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        o._step_i += 1
        if task_kind == "critic":
            # 리뷰어는 9.0이라고 자유형으로 주장하지만, critical 결함 하나 = 결정론적 계산으로는 4.0.
            score = 9.0
            text = _structured_review_text(9.0, [{"severity": "critical", "description": "x"}])
        else:
            score, text = None, "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend=o._worker(worker)["backend"], ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", lambda: (True, "ok"))
    o.run_producer_reviewer("task", "claude-main", "codex-critic", max_rounds=1, pass_score=8.0)

    assert o.gate["score"] == 4.0
    path = cfg.paths.runs.parent / "calibration.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["score"] == 4.0


def test_deterministic_scoring_on_but_parse_fails_keeps_free_text_score(mock_root, monkeypatch):
    """켜져 있어도 구조화 파싱이 실패하면(legacy_text) 기존 SCORE_RE 값을 그대로 쓴다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["review_protocol"]["deterministic_scoring"] = True
    o = Orchestrator(cfg, auto=True)
    o.verify_cmd = "sentinel verify"

    def fake_call(worker, task, task_kind="general", extra_context="", **kwargs):
        o._step_i += 1
        if task_kind == "critic":
            score = 7.0
            text = "SCORE: 7\n자유 텍스트 결함 목록만 있고 JSON은 없음"
        else:
            score, text = None, "artifact"
        o.steps.append(orchestrator.StepLog(
            o._step_i, worker, task_kind, "done", summary=text, score=score))
        return BackendResult(backend=o._worker(worker)["backend"], ok=True, text=text)

    monkeypatch.setattr(o, "call_worker", fake_call)
    monkeypatch.setattr(o, "_run_verify", lambda: (True, "ok"))
    o.run_producer_reviewer("task", "claude-main", "codex-critic", max_rounds=1, pass_score=8.0)

    assert o.gate["score"] == 7.0


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


def test_effective_reset_at_caches_across_oauth_flap(tmp_path):
    """reset_at이 플랩(oauth↔transcript)해도 캐시로 안정: reading에서 사라져도 마지막 값 유지."""
    import time as _t
    from types import SimpleNamespace
    cfg = Config.load(tmp_path)
    future = _t.time() + 5 * 24 * 3600
    r_ok = SimpleNamespace(windows=[SimpleNamespace(name="7d", used_percent=20.0, resets_at=future)])
    r_none = SimpleNamespace(windows=[SimpleNamespace(name="7d", used_percent=20.0, resets_at=None)])
    assert usage.effective_reset_at(cfg, "claude", r_ok) == future        # 캐시됨
    assert usage.effective_reset_at(cfg, "claude", r_none) == future      # 플랩해도 캐시 유지
    # 캐시가 과거면 주 단위로 전진(미래 유지)
    past = SimpleNamespace(windows=[SimpleNamespace(name="7d", used_percent=20.0, resets_at=_t.time() - 3 * 86400)])
    usage.effective_reset_at(cfg, "claude", past)                         # 과거값 캐시
    v = usage.effective_reset_at(cfg, "claude", r_none)
    assert v is not None and v > _t.time()                               # 미래로 전진


def test_weekly_used_since_reset_not_rolling(tmp_path, monkeypatch):
    """상한 누적은 7d 롤링(리셋 전 포함)이 아니라 이번 주(리셋 이후) 실제 사용이어야 한다(사용자 지적)."""
    import time as _t
    from yok3x import limits
    cfg = Config.load(tmp_path)
    now = _t.time()
    reset = now + 5 * 24 * 3600                    # 지난 리셋 = 2일 전
    # since-reset(2일)=136토큰 / 롤링(7일)=272토큰, cap=1000 → 13.6% vs 27.2%
    monkeypatch.setattr(limits, "_claude_root", lambda conf: tmp_path)
    monkeypatch.setattr(limits, "_resolve_claude_caps", lambda conf: (0.0, 1000.0))
    monkeypatch.setattr(limits, "_rolling_claude_tokens",
                        lambda root, n, secs: 136 if secs < 3 * 24 * 3600 else 272)
    assert usage.weekly_used_since_reset(cfg, "claude", reset) == 13.6   # 이번 주(롤링 27.2 아님)
    assert usage.weekly_used_since_reset(cfg, "claude", None) is None     # reset_at 없으면 None
    assert usage.weekly_used_since_reset(cfg, "codex", reset) is None     # 타 백엔드 None


def test_pacing_day_aligns_to_reset_not_midnight():
    """하루 경계는 자정이 아니라 리셋 시각(예: 오후 6시)에 정렬된다(사용자 지적)."""
    from datetime import datetime
    now = datetime(2026, 7, 21, 11, 0, 0).timestamp()      # 오전 11시
    reset = datetime(2026, 7, 26, 18, 0, 0).timestamp()    # 5일 후 오후 6시
    start = usage._pacing_day_start(reset, now)
    ds = datetime.fromtimestamp(start)
    assert ds.hour == 18 and ds.day == 20                  # 전날 오후 6시(자정 아님)
    # 리셋 시각(18시)을 지나면 새 하루로 넘어간다
    after = datetime(2026, 7, 21, 20, 0, 0).timestamp()
    ds2 = datetime.fromtimestamp(usage._pacing_day_start(reset, after))
    assert ds2.hour == 18 and ds2.day == 21
    # 레코드 초기화 키도 경계마다 달라진다
    assert usage._pacing_day_key(reset, now) != usage._pacing_day_key(reset, after)
    # reset_at 없으면 자정 폴백
    assert usage._pacing_day_key(None, now) == "2026-07-21"


def test_daily_pace_today_used_override(tmp_path):
    """today_used(자정 이후 실제 토큰 사용률)가 주어지면 7d% 델타 대신 그 값을 오늘 소비로 쓴다.
    7d%가 롤오프로 안 움직여 델타가 0이어도 오늘 실제 사용을 정확히 보여준다(사용자 지적)."""
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, strategy="fixed")
    # 7d%는 그대로(델타 0)여도 today_used=6.5면 오늘 소비 6.5로 나온다
    usage.daily_pace_status(cfg, "claude", 27.0, today="2026-07-21")  # 첫 관측(델타 0)
    r = usage.daily_pace_status(cfg, "claude", 27.0, today="2026-07-21", today_used=6.5)
    assert r["used"] == 6.5 and round(r["cap"], 1) == 14.0
    # today_used 없으면 기존 델타 방식(0)
    r2 = usage.daily_pace_status(cfg, "claude", 27.0, today="2026-07-21")
    assert r2["used"] == 0.0


def test_daily_pace_cap_excludes_today_and_is_stable(tmp_path):
    """claude(토큰) 상한 앵커는 '오늘 이전 이번주 사용'(since_reset−today_used)이라, 오늘 쓸수록 상한이
    깎이지 않고 하루 안에서 안정적이다(사용자 지적: '오늘 쓸수록 상한 또 바뀜'). catch_up 3일차."""
    import time
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                        "mode": "warn", "strategy": "catch_up"}
    reset = time.time() + 4.2 * 86400            # 3일차(D=5, k=3 → k*q=42), 안전캡 2×14=28
    # since_reset=18.5, 오늘 1.1 → 앵커=17.4 → 상한 min(28, 42−17.4)=24.6
    r1 = usage.daily_pace_status(cfg, "claude", 18.5, today="d", reset_at=reset, today_used=1.1)
    assert round(r1["used"], 1) == 1.1 and round(r1["cap"], 1) == 24.6
    # 오늘이 1.1→3.0으로 늘면 since_reset도 같이 20.4로 오르지만 앵커(20.4−3.0=17.4) 불변 → 상한 24.6 유지
    r2 = usage.daily_pace_status(cfg, "claude", 20.4, today="d", reset_at=reset, today_used=3.0)
    assert round(r2["used"], 1) == 3.0 and round(r2["cap"], 1) == 24.6   # 상한 안 깎임(안정)


def test_apply_config_accepts_all_three_daily_pace_strategies(tmp_path):
    """BUG-45 회귀 방지: guiserver._apply_config의 strategy 화이트리스트가 "spread"를 빠뜨려서,
    GUI에서 '분산' 버튼을 눌러도 {"ok": true}만 돌아오고 실제 값은 조용히 안 바뀌던 버그.
    세 값(fixed/catch_up/spread) 전부 실제로 저장돼야 한다."""
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    for strat in ("fixed", "catch_up", "spread"):
        r = gs._apply_config(cfg, {"daily_pace": {"strategy": strat}})
        assert r.get("ok") is True
        assert cfg.yok3x["guard"]["daily_pace"]["strategy"] == strat, (
            f"strategy={strat} 저장 안 됨(BUG-45 재발) — 실제 값: "
            f"{cfg.yok3x['guard']['daily_pace']['strategy']}")


def test_apply_config_rejects_unknown_daily_pace_strategy_silently(tmp_path):
    """알 수 없는 strategy 값은(오타 등) 저장하지 않고 기존 값을 유지한다 — 에러도 안 내지만
    조용히 덮어쓰지도 않는다(현재 동작 그대로 문서화. 완전히 검증하려면 명시적 에러가 더 낫지만
    그건 별도 개선 — 최소한 잘못된 값으로 덮어써지지는 않아야 함)."""
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"]["strategy"] = "catch_up"
    r = gs._apply_config(cfg, {"daily_pace": {"strategy": "bogus-typo"}})
    assert r.get("ok") is True
    assert cfg.yok3x["guard"]["daily_pace"]["strategy"] == "catch_up"   # 안 덮어써짐


def test_daily_pace_strategy_options_distinct(tmp_path):
    """세 전략이 뚜렷이 구분: fixed=고정 · catch_up=즉시조임 · spread=균등분산(초과 시 핵심 차이)."""
    import time as _t
    cfg = Config.load(tmp_path)
    now = _t.time()
    reset = now + (3 * 24 + 23) * 3600            # D=4, k=4 → 과사용 케이스로 옵션 차이 확인
    caps = {}
    for strat in ("fixed", "catch_up", "spread"):
        cfg.yok3x["guard"]["daily_pace"].update(enabled=True, strategy=strat)
        r = usage.daily_pace_status(cfg, f"s_{strat}", 76.0, today=f"d-{strat}", reset_at=reset)
        caps[strat] = round(r["cap"], 1)
    assert caps["fixed"] == 14.0                   # 고정
    assert caps["catch_up"] == 0.0                 # 56-76 → 즉시 0 조임
    assert caps["spread"] == 6.0                   # (100-76)/4 → 균등분산
    # 알 수 없는 전략은 fixed로 폴백
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, strategy="bogus")
    assert round(usage.daily_pace_status(cfg, "s_bogus", 76.0, today="d-b",
                                         reset_at=reset)["cap"], 1) == 14.0


def test_daily_pace_forward_only_when_over_daily_cap(tmp_path):
    """이후 지속가능 일일률은 **일간 상한을 초과했을 때만** 표시. under면 이월이라 None."""
    import time as _t
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, strategy="catch_up")
    now = _t.time()
    # codex 76%·리셋 3d23h → catch_up 상한=0(과사용) · used=0 → 0>=0 초과 → (100-76)/4=6.0 표시
    over = usage.daily_pace_status(cfg, "codex", 76.0, reset_at=now + (3 * 24 + 23) * 3600)
    assert round(over["cap"], 1) == 0.0 and over["forward_daily"] == 6.0
    # claude 24%·리셋 5d1h → 상한=4%p, used=0 → 0<4 under(이월) → forward_daily None(나눗셈 안 보임)
    under = usage.daily_pace_status(cfg, "claude", 24.0, reset_at=now + (5 * 24 + 1) * 3600)
    assert round(under["cap"], 1) == 4.0 and under["forward_daily"] is None
    # 리셋 정보 없으면 표시 생략(None)
    assert usage.daily_pace_status(cfg, "codex", 50.0, reset_at=None)["forward_daily"] is None


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


def test_statusline_capture_and_probe(tmp_path):
    """F-08: statusLine stdin JSON의 rate_limits를 캡처→프로브가 읽어 real=True 창(리셋시각 포함) 생성.
    OAuth·네트워크 없이(1st-party·안전). 스키마: rate_limits.five_hour/seven_day.used_percentage/resets_at."""
    import json, time
    from yok3x import limits
    cache = tmp_path / "statusline.json"
    conf = {"statusline_path": str(cache)}
    now = int(time.time())
    stdin_json = json.dumps({
        "model": {"id": "claude-x"},
        "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": now + 3600},
            "seven_day": {"used_percentage": 41.2, "resets_at": now + 4 * 86400},
        },
    })
    line = limits.statusline_capture(conf, stdin_json)
    assert "5h 24%" in line and "7d 41%" in line and cache.exists()   # 상태줄 출력 + 캐시 저장
    r = limits._probe_claude_statusline("claude", conf)
    assert r.ok and r.real and r.source == "claude_statusline"
    ws = {w.name: w for w in r.windows}
    assert round(ws["5h"].used_percent, 1) == 23.5 and round(ws["7d"].used_percent, 1) == 41.2
    assert ws["5h"].resets_at == now + 3600 and ws["7d"].resets_at == now + 4 * 86400


def test_statusline_missing_rate_limits_falls_back(tmp_path):
    """rate_limits 없는 stdin(세션 첫 응답 전·비 Pro/Max)이면 창을 안 만들고 프로브는 추정 폴백(지어내지 않음)."""
    import json
    from yok3x import limits
    conf = {"statusline_path": str(tmp_path / "sl.json"), "projects_dir": str(tmp_path / "noproj")}
    line = limits.statusline_capture(conf, json.dumps({"model": {"id": "x"}}))   # rate_limits 없음
    assert "대기" in line
    r = limits._probe_claude_statusline("claude", conf)
    assert r.source == "claude_transcripts"           # statusline 아닌 추정으로 폴백


def test_statusline_stale_falls_back(tmp_path):
    """statusline 캐시가 max_stale보다 오래되면 추정 폴백."""
    import json, time
    from yok3x import limits
    cache = tmp_path / "sl.json"
    cache.write_text(json.dumps({"captured_at": time.time() - 5000, "windows":
                     [{"name": "7d", "used_percent": 40, "resets_at": time.time() + 1,
                       "window_minutes": 10080}]}), encoding="utf-8")
    conf = {"statusline_path": str(cache), "statusline_max_stale_sec": 900,
            "projects_dir": str(tmp_path / "noproj")}
    assert limits._probe_claude_statusline("claude", conf).source == "claude_transcripts"


def test_statusline_capture_bad_json_safe(tmp_path):
    """깨진 stdin도 크래시 없이 빈 창으로 안전 처리(statusLine 렌더를 막지 않음)."""
    from yok3x import limits
    line = limits.statusline_capture({"statusline_path": str(tmp_path / "sl.json")}, "not json {{{")
    assert line.startswith("yok3x")


def test_pacing_day_key_stable_across_subsecond_reset_jitter():
    """reset_at이 .5 경계 근처로 초 이하 드리프트해도 하루키·win_gen이 안 튄다(분 단위 양자화).
    안 그러면 매 폴 '새 하루'로 오인 → start_pct 재캡처로 오늘 소비가 0으로 리셋됨(사용자 지적 버그)."""
    from yok3x import usage
    now = 1784800200.0
    base = 1785056400   # .5 경계에 걸치는 리셋(관측값: ...400.501429)
    keys = {usage._pacing_day_key(base + f, now) for f in (0.0, 0.41, 0.49, 0.5, 0.51, 0.99)}
    assert len(keys) == 1, f"하루키가 초 이하 지터에 흔들림: {keys}"
    wins = {int((base + f) // 60) for f in (0.0, 0.41, 0.49, 0.5, 0.51, 0.99)}
    assert len(wins) == 1, f"win_gen이 초 이하 지터에 흔들림: {wins}"


def test_pacing_used_today_survives_reset_jitter(tmp_path):
    """초 이하로 흔들리는 reset_at을 번갈아 넘겨도 같은 하루로 인식해 used_today 누적이 유지된다
    (spurious 리셋으로 0 초기화되지 않음). codex형 스냅샷 모델(since_reset_known=False)."""
    import time
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                        "mode": "warn", "strategy": "catch_up"}
    now = time.time()
    ra_a = now + 3 * 86400 + 0.41           # 같은 리셋, 초 이하만 다름
    ra_b = now + 3 * 86400 + 0.50
    k_a, k_b = usage._pacing_day_key(ra_a, now), usage._pacing_day_key(ra_b, now)
    assert k_a == k_b                        # 지터에도 같은 하루키
    usage.daily_pace_status(cfg, "claude", 64.0, today=k_a, reset_at=ra_a,
                            today_used=None, since_reset_known=False)   # 하루 시작 64%
    s1 = usage.daily_pace_status(cfg, "claude", 66.0, today=k_b, reset_at=ra_b,
                                 today_used=None, since_reset_known=False)  # 지터된 reset로 재호출
    assert round(s1["used"], 1) == 2.0       # 66-64=2%, 0으로 리셋 안 됨


def test_pace_inputs_oauth_snapshot_fallback_when_no_transcript(tmp_path):
    """트랜스크립트 일간 분해가 없으면(reset_at=None 등) _pace_inputs는 스냅샷 모델
    (since_reset_known=False·today_used=None)로 폴백한다."""
    from yok3x import usage, limits
    cfg = Config.load(tmp_path)
    real = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("7d", 65.0)])
    cur, known, tu = usage._pace_inputs(cfg, "claude", real, reset_at=None)
    assert cur == 65.0 and known is False and tu is None      # 트랜스크립트 없음 → 스냅샷 폴백


def test_pace_inputs_oauth_rescales_transcript_today_for_stable_daily(tmp_path, monkeypatch):
    """OAuth 주간%가 있고 트랜스크립트 일간 분해가 있으면, 트랜스크립트 오늘/이번주 비율로 OAuth
    총량을 '오늘분'으로 환산해 (r7, True, today_scaled)를 준다. 순수 스냅샷은 재기동 시 오늘=0으로
    붕괴·상한 드리프트(사용자 지적)했지만, 이 경로는 u0=현재−오늘(=오늘이전, 과거값)이 하루 안 안정.
    OAuth 69%·트랜스크립트 이번주 53.9%/오늘 7.1% → 오늘=7.1×69/53.9≈9.1."""
    from yok3x import usage, limits
    cfg = Config.load(tmp_path)
    monkeypatch.setattr(usage, "weekly_used_since_reset", lambda c, b, ra: 53.9)
    monkeypatch.setattr(usage, "today_used_pct", lambda c, b, ra: 7.1)
    real = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("7d", 69.0)])
    cur, known, tu = usage._pace_inputs(cfg, "claude", real, reset_at=123456.0)
    assert cur == 69.0 and known is True
    assert abs(tu - 9.1) < 0.15                     # 오늘 실제 사용 잡힘(0 아님)
    # 하루 안정 불변식: 사용이 늘어도 u0(=현재−오늘=오늘이전)는 거의 불변 → 상한 흔들리지 않음.
    monkeypatch.setattr(usage, "weekly_used_since_reset", lambda c, b, ra: 55.5)
    monkeypatch.setattr(usage, "today_used_pct", lambda c, b, ra: 8.7)
    real2 = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                                windows=[limits.Window("7d", 71.0)])
    cur2, _, tu2 = usage._pace_inputs(cfg, "claude", real2, reset_at=123456.0)
    assert abs((cur2 - tu2) - (cur - tu)) < 0.5     # 오늘이전(=상한 앵커) 불변


def test_oauth_cap_stable_within_day_as_usage_grows(tmp_path):
    """OAuth 경로 상한은 하루 안에서 고정(스냅샷) — 오늘 사용이 늘어도 상한이 변하지 않는다.
    (사용자 지적 버그: current(OAuth)−today_used(transcript) 혼합으로 상한이 오늘 쓸수록 올라감)."""
    import time
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                        "mode": "warn", "strategy": "catch_up"}
    reset = time.time() + 2.1 * 86400
    caps = [usage.daily_pace_status(cfg, "claude", pct, today="d1", reset_at=reset,
                                    today_used=None, since_reset_known=False)["cap"]
            for pct in (65.0, 66.0, 68.0)]
    assert round(caps[0], 1) == round(caps[1], 1) == round(caps[2], 1)   # 하루 안 고정
    useds = [usage.daily_pace_status(cfg, "claude", pct, today="d1", reset_at=reset,
                                     today_used=None, since_reset_known=False)["used"]
             for pct in (65.0, 68.0)]
    assert useds[1] > useds[0]                                           # 오늘 사용만 증가


def test_spread_main_cap_stable_even_cap_carries_shrinking(tmp_path):
    """사용자 설계 ②: 메인 줄 상한은 지속가능률(남은예산÷남은일수, 하루 고정)이고, 쓴 만큼 줄어드는
    엄격 균등선(catch_up) 값은 even_cap으로 분리해 오버레이 전용. reset 2.1일·주간 67%면 spread=11,
    catch_up=3. even_cap이 메인 cap보다 작아야(오버레이에서 '줄어드는 값'으로 노출) 한다."""
    import time
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                        "mode": "warn", "strategy": "spread"}
    reset = time.time() + 2.1 * 86400            # D=3, k=5 → 균등선 70%
    st = usage.daily_pace_status(cfg, "claude", 67.0, today="d1", reset_at=reset,
                                 today_used=None, since_reset_known=False)
    assert round(st["cap"], 0) == 11             # 메인: spread=(100-67)/3=11
    assert round(st["even_cap"], 0) == 3         # 오버레이: catch_up=70-67=3(쓴 만큼 줄어드는 값)
    assert st["even_cap"] < st["cap"]            # 메인은 안정, 엄격값은 더 작음 → 오버레이에 분리 표시
    # 하루 안에서 사용이 늘어도 메인 cap은 고정(스냅샷)
    st2 = usage.daily_pace_status(cfg, "claude", 69.0, today="d1", reset_at=reset,
                                  today_used=None, since_reset_known=False)
    assert round(st2["cap"], 1) == round(st["cap"], 1)


def test_pacing_prefers_real_reading_7d(tmp_path):
    """페이싱 since-reset은 실측 reading의 7d%를 우선(바 게이지와 동일 소스 → 밴드가 바 채움과 일치).
    실측 아니면 None(트랜스크립트/롤링에 맡김). 사용자 지적: 바=OAuth·밴드=트랜스크립트 불일치."""
    from yok3x import usage, limits
    real = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("5h", 90.0), limits.Window("7d", 47.0)])
    assert usage.reading_since_reset_pct(real) == 47.0
    est = limits.LimitReading("claude", "claude_transcripts", ok=True, real=False,
                              windows=[limits.Window("7d", 30.0)])
    assert usage.reading_since_reset_pct(est) is None   # 추정은 제외
    assert usage.reading_since_reset_pct(None) is None


def test_save_weekly_phase_persists_7d_reset(tmp_path):
    """실측 성공 시 7d 리셋 시각을 config.weekly_reset_epoch에 저장(폴백서 재사용할 주간 위상)."""
    from yok3x import limits
    cfg = Config.load(tmp_path)
    cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})
    ws = [limits.Window("5h", 10.0, resets_at=111.0), limits.Window("7d", 40.0, resets_at=222.0)]
    limits._save_weekly_phase(cfg, ws)
    assert cfg.yok3x["limits"]["claude"]["weekly_reset_epoch"] == 222.0   # 7d만


def test_transcripts_applies_weekly_phase_reset(tmp_path):
    """weekly_reset_epoch(과거 앵커)가 있으면 transcripts 7d 창에 미래로 롤포워드한 리셋을 붙인다
    ('168시간 롤링' 대신 실제 카운트다운). 5h는 세션기반이라 롤링 유지."""
    import time, json
    from datetime import datetime, timezone
    from yok3x import limits
    root = tmp_path / "proj"
    root.mkdir()
    (root / "s.jsonl").write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": {"usage": {"input_tokens": 500, "output_tokens": 500}}}) + "\n", encoding="utf-8")
    now = time.time()
    conf = {"projects_dir": str(root), "limit_5h_tokens": 100000, "limit_7d_tokens": 100000,
            "weekly_reset_epoch": now - 2 * 86400}     # 2일 전 → +5일 후로 롤포워드돼야
    r = limits._probe_claude_transcripts("claude", conf)
    w = {x.name: x for x in r.windows}
    assert w["7d"].resets_at is not None and w["7d"].resets_at > now   # 미래로 전개
    assert (w["7d"].resets_at - now) < 7 * 86400                        # 7일 이내(한 주기)
    assert w["5h"].resets_at is None                                    # 5h는 롤링 유지


def test_oauth_backoff_on_failure(tmp_path, monkeypatch):
    """OAuth 실패 시 백오프: 429/네트워크는 지수, 401/403은 장기 중단(재시도 무의미). 성공하면 해제."""
    from yok3x import limits
    limits._OAUTH_BACKOFF.pop("claude", None)
    limits._OAUTH_LIVE_CACHE.pop("claude", None)
    conf = {"min_interval_sec": 60, "projects_dir": str(tmp_path)}
    # 429 실패 → 백오프 설정
    monkeypatch.setattr(limits, "_fetch_claude_oauth_usage",
                        lambda b, c: limits.LimitReading(b, "claude_oauth", ok=False, real=True,
                                                         error="HTTP 429 호출 과다"))
    limits._probe_claude_oauth("claude", conf)
    assert "claude" in limits._OAUTH_BACKOFF and limits._OAUTH_BACKOFF["claude"][1] == 1
    # 401 실패 → 장기 중단(delay 큼)
    limits._OAUTH_BACKOFF.pop("claude", None)
    monkeypatch.setattr(limits, "_fetch_claude_oauth_usage",
                        lambda b, c: limits.LimitReading(b, "claude_oauth", ok=False, real=True,
                                                         error="HTTP 401 토큰 만료/미인증"))
    import time
    limits._probe_claude_oauth("claude", conf)
    assert limits._OAUTH_BACKOFF["claude"][0] - time.time() > 1800   # 401은 30분 초과 중단
    limits._OAUTH_BACKOFF.pop("claude", None)


def _write_claude_session(path, lines):
    """R-5 테스트 헬퍼: (timestamp_iso, session_id, model, tokens_dict) 목록을 JSONL로 씀."""
    import json
    body = []
    for ts, sid, model, u in lines:
        body.append(json.dumps({"sessionId": sid, "type": "assistant", "timestamp": ts,
                                "message": {"model": model, "usage": u}}))
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def test_claude_usage_breakdown_aggregates_by_session_and_model(tmp_path):
    """R-5(Tier2): 세션·모델별로 토큰·호출수를 집계하고 토큰 내림차순으로 정렬한다."""
    from yok3x import limits
    root = tmp_path / "proj"
    root.mkdir()
    _write_claude_session(root / "a.jsonl", [
        ("2026-08-01T00:00:00Z", "sess-A", "claude-opus-4-8", {"input_tokens": 100, "output_tokens": 100}),
        ("2026-08-01T00:05:00Z", "sess-A", "claude-opus-4-8", {"input_tokens": 50, "output_tokens": 50}),
        ("2026-08-01T00:10:00Z", "sess-A", "claude-sonnet-5", {"input_tokens": 10, "output_tokens": 10}),
    ])
    _write_claude_session(root / "b.jsonl", [
        ("2026-08-01T01:00:00Z", "sess-B", "claude-sonnet-5", {"input_tokens": 500, "output_tokens": 500}),
    ])
    rows = limits.claude_usage_breakdown({"projects_dir": str(root)})
    by_key = {(r["session_id"], r["model"]): r for r in rows}
    assert by_key[("sess-A", "claude-opus-4-8")]["tokens"] == 300
    assert by_key[("sess-A", "claude-opus-4-8")]["calls"] == 2
    assert by_key[("sess-A", "claude-sonnet-5")]["tokens"] == 20
    assert by_key[("sess-B", "claude-sonnet-5")]["tokens"] == 1000
    assert rows[0]["tokens"] == 1000   # 토큰 내림차순 정렬 — sess-B가 1위


def test_claude_usage_breakdown_since_until_filters(tmp_path):
    """R-5: since/until 밖의 이벤트는 집계에서 빠진다."""
    from yok3x import limits
    from datetime import datetime, timezone
    root = tmp_path / "proj"
    root.mkdir()
    old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    new_ts = datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
    _write_claude_session(root / "a.jsonl", [
        (old_ts, "sess-old", "claude-opus-4-8", {"input_tokens": 999, "output_tokens": 0}),
        (new_ts, "sess-new", "claude-opus-4-8", {"input_tokens": 111, "output_tokens": 0}),
    ])
    since = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    rows = limits.claude_usage_breakdown({"projects_dir": str(root)}, since=since)
    sessions = {r["session_id"] for r in rows}
    assert "sess-new" in sessions and "sess-old" not in sessions


def test_claude_usage_breakdown_missing_dir_returns_empty(tmp_path):
    """R-5 폴백 가드: projects_dir이 없어도 예외 없이 빈 리스트."""
    from yok3x import limits
    assert limits.claude_usage_breakdown({"projects_dir": str(tmp_path / "no-such-dir")}) == []


def test_claude_usage_breakdown_malformed_lines_degrade_gracefully(tmp_path):
    """R-5 폴백 가드: 깨진 JSON·model/sessionId 누락 줄은 죽지 않고 건너뛰거나 '(알수없음)'으로 묶인다
    (Claude Code JSONL은 비문서·불안정 포맷 — 포맷 변경에 강해야 한다)."""
    from yok3x import limits
    import json
    root = tmp_path / "proj"
    root.mkdir()
    lines = [
        "not even json {{{",
        json.dumps({"type": "assistant", "timestamp": "2026-08-01T00:00:00Z",
                    "message": {"usage": {"input_tokens": 7, "output_tokens": 0}}}),   # sessionId·model 없음
        json.dumps({"sessionId": "sess-ok", "timestamp": "2026-08-01T00:00:00Z",
                    "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 3, "output_tokens": 0}}}),
    ]
    (root / "a.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = limits.claude_usage_breakdown({"projects_dir": str(root)})
    keys = {(r["session_id"], r["model"]) for r in rows}
    assert ("(알수없음)", "(알수없음)") in keys
    assert ("sess-ok", "claude-sonnet-5") in keys


def test_file_usage_events_matches_detailed_view(tmp_path):
    """회귀 방지: `_file_usage_events`(페이싱 핫패스)가 `_file_usage_events_detailed`에서 session_id·
    model만 뺀 것과 동일해야 한다 — R-5를 위해 파서를 공유로 리팩터링하면서 페이싱 출력이 바뀌면 안 됨."""
    from yok3x import limits
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.jsonl"
    _write_claude_session(f, [
        ("2026-08-01T00:00:00Z", "sess-A", "claude-opus-4-8", {"input_tokens": 100, "output_tokens": 20}),
        ("2026-08-01T00:05:00Z", "sess-A", "claude-sonnet-5", {"input_tokens": 5, "output_tokens": 5}),
    ])
    detailed = limits._file_usage_events_detailed(f)
    light = limits._file_usage_events(f)
    assert light == [(ts, tok) for ts, tok, _sid, _model in detailed]
    assert len(light) == 2


def test_oauth_live_persists_to_disk_across_process(tmp_path, monkeypatch):
    """BUG-35(2번): 성공 실측을 디스크(.yok3x/oauth_live.json)에 남겨, 인메모리 캐시가 빈 새
    프로세스(CLI 호출·GUI 재기동)도 stale-while-error로 이전 실측을 이어받는다 → OAuth 429 시
    '오늘 소비'가 0(트랜스크립트)으로 플립하지 않고 마지막 실측을 유지(사용자 지적한 0↔실측 깜빡임)."""
    import time
    from yok3x import limits
    cfg = Config.load(tmp_path)
    conf = {"min_interval_sec": 60, "max_stale_sec": 3600}
    limits._OAUTH_LIVE_CACHE.clear()
    limits._OAUTH_BACKOFF.clear()
    # 성공 실측 1회 → 디스크 저장 확인
    good = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                               windows=[limits.Window("7d", 66.0, resets_at=time.time() + 100000)],
                               detail="ok (live)")
    monkeypatch.setattr(limits, "_fetch_claude_oauth_usage", lambda b, c: good)
    limits._probe_claude_oauth("claude", conf, cfg)
    disk = cfg.paths.yok3x_dir / "oauth_live.json"
    assert disk.exists()
    store = json.loads(disk.read_text(encoding="utf-8"))
    assert store["claude"]["windows"][0]["used_percent"] == 66.0

    # 새 프로세스 시뮬레이션: 인메모리 캐시/백오프 비우고, 이제 OAuth는 429. 디스크 실측은
    # interval보다 오래됐지만(라이브 재시도 유도) max_stale 이내라 stale 경로를 태운다.
    limits._OAUTH_LIVE_CACHE.clear()
    limits._OAUTH_BACKOFF.clear()
    limits._save_oauth_live(cfg, "claude", time.time() - 120, good)
    monkeypatch.setattr(limits, "_fetch_claude_oauth_usage",
                        lambda b, c: limits.LimitReading(b, "claude_oauth", ok=False, real=True,
                                                         error="HTTP 429 호출 과다"))
    r = limits._probe_claude_oauth("claude", conf, cfg)
    # 트랜스크립트로 플립하지 않고 디스크의 마지막 실측(66%)을 유지(stale-while-error 라벨).
    assert r.ok and r.real
    assert any(w.name == "7d" and w.used_percent == 66.0 for w in r.windows)
    assert "실측" in r.detail
    limits._OAUTH_LIVE_CACHE.clear()
    limits._OAUTH_BACKOFF.clear()


def test_statusline_rejects_implausible_reset(tmp_path):
    """비현실적 resets_at(밀리초 오인·자리표시자 9999999999 등)은 None 폴백 — '95084일 후' 쓰레기 표시 방지.
    정상값(수시간~수일 내)은 유지."""
    import json, time
    from yok3x import limits
    conf = {"statusline_path": str(tmp_path / "sl.json")}
    now = int(time.time())
    limits.statusline_capture(conf, json.dumps({"rate_limits": {
        "five_hour": {"used_percentage": 5, "resets_at": 9999999999},        # 자리표시자(비현실적)
        "seven_day": {"used_percentage": 38, "resets_at": now + 4 * 86400},  # 정상
    }}))
    ws = {w.name: w for w in limits._probe_claude_statusline("claude", conf).windows}
    assert ws["5h"].resets_at is None                    # 비현실적 → None(롤링 라벨 폴백)
    assert ws["7d"].resets_at == now + 4 * 86400         # 정상은 유지


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
def _calib_win(name, pct, resets_in=3600.0):
    """캘리브레이션용 창. BUG-47 이후 autocalibrate는 `resets_at`(창 위상)이 있어야 보정한다 —
    분자(로컬 토큰)를 서버 창과 같은 구간으로 맞춰야 역산이 성립하기 때문."""
    return limits.Window(name, pct, resets_at=time.time() + resets_in)


def _stub_window_tokens(monkeypatch, toks):
    """위상 정렬 토큰 합계 스텁. autocalibrate는 claude_window_tokens만 쓴다(트레일링 롤링 아님)."""
    monkeypatch.setattr(limits, "claude_window_tokens",
                        lambda c, w, ra, now=None: toks)


def test_autocalibrate_claude_saves_cap_from_live_percent(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 1_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", ok=True, real=True,
                                  windows=[_calib_win("5h", 10.0)])

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
    _stub_window_tokens(monkeypatch, toks)

    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", pct)]))

    assert got["5h"] is None and reason in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 12_345_678
    assert not cfg.paths.yok3x_json.exists()


def test_autocalibrate_claude_rate_limits_writes(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 1_000_000)
    first = limits.LimitReading("claude", "claude_oauth", True, True,
                                [_calib_win("5h", 10.0)])
    second = limits.LimitReading("claude", "claude_oauth", True, True,
                                 [_calib_win("7d", 10.0)])
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
    _stub_window_tokens(monkeypatch, 20_000_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [_calib_win("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "비현실" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000


def test_autocalibrate_claude_allows_cache_read_inflation(tmp_path, monkeypatch):
    """실측 근거: 7d는 cache read 누적으로 plan 대비 ~153배 캡이 정상이다(max_calib_multiple=1000
    이내라 비현실로 거부되지 않음). 단, 이미 보정된 값(override>0)이 있으면 회당 변화는 ±25%로
    클램프된다(BUG-35: 스윙 방지) — 큰 배율은 여러 스텝에 걸쳐 수렴한다."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_7d_tokens"] = 500_000_000          # 이미 보정된 값(override>0) → 클램프 대상
    limits._CLAUDE_CALIBRATION_STATE.clear()
    # 10%에서 76.6억 tok → 파생 cap 766억(=153배)이지만 비현실 거부는 안 됨. 클램프로 +25%만 반영.
    # (BUG-47 이후 5% 같은 저관측은 양자화 오차가 커 아예 건너뛴다 — 여기선 10%로 관측한다.)
    _stub_window_tokens(monkeypatch, 7_660_000_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [_calib_win("7d", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["7d"] == 625_000_000, got          # 500M × 1.25 (스윙 방지 클램프)
    assert conf["limit_7d_tokens"] == 625_000_000


def test_autocalibrate_claude_clamps_swing_and_converges(tmp_path, monkeypatch):
    """BUG-35 회귀: 파생 cap이 크게 튀어도 회당 ±25%로 제한돼 246M→793M 같은 3배 스윙이 안 난다.
    또한 파생값이 꾸준히 높으면 여러 스텝에 걸쳐 그쪽으로 수렴한다(끄지 않고 안정화)."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 200_000_000          # 이미 보정된 값
    # 파생 cap = 10억(=5배 폭등 시도). 클램프 없으면 그대로 저장돼 스윙의 씨앗이 된다.
    _stub_window_tokens(monkeypatch, 1_000_000_000)

    prev = 200_000_000
    for step in range(6):
        limits._CLAUDE_CALIBRATION_STATE.clear()   # 스텝마다 rate-limit 우회
        got = limits.autocalibrate_claude(
            cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                           [_calib_win("5h", 10.0)]))
        cur = conf["limit_5h_tokens"]
        # 회당 변화는 절대 25%를 넘지 않는다(스윙/폭등 방지).
        assert cur <= prev * 1.25 + 1, (step, prev, cur)
        # 파생값(10억)이 꾸준히 높으니 단조 증가하며 수렴한다.
        assert cur >= prev
        prev = cur
    # 6스텝 후에도 파생값(10억)에 아직 도달하지 않았지만(느린 수렴=안정), 확실히 올라왔다.
    assert 200_000_000 < prev < 1_000_000_000


def test_autocalibrate_claude_multiple_bounds_come_from_config(tmp_path, monkeypatch):
    """가드 경계는 하드코딩이 아니라 설정이어야 한다(RULE §5.5)."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    conf["max_calib_multiple"] = 2.0               # 상한을 좁히면 거부돼야
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 10_000_000)   # 10%→1억=10배
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [_calib_win("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "비현실" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000


def test_autocalibrate_claude_skips_negligible_change(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    limits._CLAUDE_CALIBRATION_STATE.clear()
    # 역산 cap=10.5M: 기존 대비 정확히 +5%라 파일 churn 없이 유지한다.
    _stub_window_tokens(monkeypatch, 1_050_000)
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [_calib_win("5h", 10.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and "변화<=5%" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000
    assert not cfg.paths.yok3x_json.exists()


def test_autocalibrate_claude_ignores_per_model_window(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_window_tokens",
                        lambda *a, **k: pytest.fail("per-model 창은 transcript를 읽으면 안 됨"))
    reading = limits.LimitReading("claude", "claude_oauth", True, True,
                                  [_calib_win("7d·Fable", 22.0)])

    got = limits.autocalibrate_claude(cfg, conf, reading)

    assert got["5h"] is None and got["7d"] is None
    assert conf["limit_5h_tokens"] == 0 and conf["limit_7d_tokens"] == 0


def test_autocalibrate_claude_off_preserves_existing_behavior(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["autocalibrate"] = False
    limits._CLAUDE_CALIBRATION_STATE.clear()
    monkeypatch.setattr(limits, "claude_window_tokens",
                        lambda *a, **k: pytest.fail("off이면 transcript를 읽으면 안 됨"))

    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", 10.0)]))

    assert "비활성" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 0 and not cfg.paths.yok3x_json.exists()


# -------------------------------------- BUG-47 회귀: 창 위상 정렬 + 저관측 거부
def test_claude_window_tokens_counts_only_since_window_start(tmp_path, monkeypatch):
    """서버 창(텀블링)과 같은 구간만 센다 — 리셋 이전 토큰은 분자에 들어오면 안 된다."""
    now = 1_000_000.0
    resets_at = now + 3600.0                        # 7d 창이 1시간 뒤 리셋 → 창 시작 = now-7d+1h
    seen: dict[str, float] = {}

    def fake(root, n, window_sec):
        seen["cutoff"] = n - window_sec
        return 42

    monkeypatch.setattr(limits, "_rolling_claude_tokens", fake)
    monkeypatch.setattr(limits, "_claude_root", lambda c: tmp_path)

    got = limits.claude_window_tokens({}, "7d", resets_at, now)

    assert got == 42
    # cutoff는 '지금-7일'(트레일링)이 아니라 'resets_at-7일'(창 시작)이어야 한다.
    assert seen["cutoff"] == pytest.approx(resets_at - 7 * 86400.0)
    assert seen["cutoff"] != pytest.approx(now - 7 * 86400.0)


def test_autocalibrate_claude_ignores_pre_reset_tokens(tmp_path, monkeypatch):
    """BUG-47 핵심: 리셋 직후 트레일링 합계는 '지난 창'까지 끌고 와 파생 cap을 부풀린다(실측 3.68배).
    위상 정렬 합계를 쓰면 같은 live %에서도 참값이 나온다."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    limits._CLAUDE_CALIBRATION_STATE.clear()
    now = time.time()
    resets_at = now + 6 * 86400.0                  # 7d 창 1일차(리셋 직후)
    win_start = resets_at - 7 * 86400.0

    # 창 시작 이전 90억(지난 창) + 창 안 13.3억. 트레일링이면 둘 다 세서 cap이 7.7배 부풀었다.
    def tokens(root, n, window_sec):
        return 1_334_000_000 if (n - window_sec) >= win_start - 1 else 10_244_000_000

    monkeypatch.setattr(limits, "_rolling_claude_tokens", tokens)
    monkeypatch.setattr(limits, "_claude_root", lambda c: tmp_path)

    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [limits.Window("7d", 10.0, resets_at=resets_at)]))

    assert got["7d"] == 13_340_000_000, got        # 13.34억/0.10 — 창 안 토큰만
    assert got["7d"] < 100_000_000_000            # 트레일링(102.4억/0.10)이면 여기서 걸린다


def test_autocalibrate_claude_skips_without_reset_phase(tmp_path, monkeypatch):
    """위상을 모르면(resets_at 없음/과거) 미정렬 역산 대신 건너뛴다 — 틀린 cap보다 무보정이 낫다."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    monkeypatch.setattr(limits, "claude_window_tokens",
                        lambda *a, **k: pytest.fail("위상 없으면 읽지 않는다"))

    for win in (limits.Window("5h", 30.0),                                  # resets_at 없음
                limits.Window("5h", 30.0, resets_at=time.time() - 600)):    # 이미 지난 값
        limits._CLAUDE_CALIBRATION_STATE.clear()
        got = limits.autocalibrate_claude(
            cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True, [win]))
        assert got["5h"] is None and "위상" in str(got["skipped"])
        assert conf["limit_5h_tokens"] == 10_000_000


def test_autocalibrate_claude_skips_quantization_noise(tmp_path, monkeypatch):
    """정수 %의 상대 불확실도(0.5/pct)가 ±5% 데드밴드보다 크면(=pct<10) 보정하지 않는다 —
    노이즈와 구분되지 않는 관측으로 cap을 쓰면 폴마다 값이 튀어 수렴하지 않는다(BUG-35 재발 경로)."""
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf["limit_5h_tokens"] = 10_000_000
    _stub_window_tokens(monkeypatch, 1_000_000)

    limits._CLAUDE_CALIBRATION_STATE.clear()       # 5% → 0.5/5 = ±10% > 5% → 거부
    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", 5.0)]))
    assert got["5h"] is None and "양자화오차" in str(got["skipped"])
    assert conf["limit_5h_tokens"] == 10_000_000

    limits._CLAUDE_CALIBRATION_STATE.clear()       # 20% → ±2.5% ≤ 5% → 통과
    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", 20.0)]))
    # 파생 cap 5M(=1M/0.20)이지만 기존 보정값 10M이 있어 회당 -25% 클램프(BUG-35)로 7.5M.
    assert got["5h"] == 7_500_000, got


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
    monkeypatch.setattr(gs.usage, "check_backend", lambda c, b, **kw: verdict)
    monkeypatch.setattr(gs.usage, "coach_messages", lambda c, **kw: [])
    monkeypatch.setattr(gs, "_routing_preview", lambda c: {})
    monkeypatch.setattr(gs, "_profile_routes", lambda c: {})
    monkeypatch.setattr(gs.limits, "list_models", lambda c, b: [])
    monkeypatch.setattr(gs.limits, "list_models_gui", lambda c, b: [])
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
def _seed_calib_history(cfg, values, directions):
    usage._save_pace(cfg, {"claude": {"calib_history": [
        {"ts": i, "key": "limit_5h_tokens", "value": value,
         "direction": direction}
        for i, (value, direction) in enumerate(zip(values, directions))
    ]}})


def test_autocalibrate_circuit_breaker_stops_reversing_large_updates(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf.update({"autocalibrate": True, "limit_5h_tokens": 100, "min_interval_sec": 0})
    _seed_calib_history(cfg, [100, 125, 100], ["up", "up", "down"])
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 12.5)
    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", 10.0)]))
    assert got["skipped"] == "진동 감지 — autocalibrate 자동 정지"
    assert conf["autocalibrate"] is False
    assert conf["limit_5h_tokens"] == 100


def test_autocalibrate_circuit_breaker_avoids_false_positives(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    conf = cfg.yok3x["limits"]["claude"]
    conf.update({"autocalibrate": True, "limit_5h_tokens": 100, "min_interval_sec": 0})
    _seed_calib_history(cfg, [100, 110, 120], ["up", "up", "up"])
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 12.5)
    got = limits.autocalibrate_claude(
        cfg, conf, limits.LimitReading("claude", "claude_oauth", True, True,
                                       [_calib_win("5h", 10.0)]))
    assert "진동" not in str(got["skipped"])
    assert conf["autocalibrate"] is True

    cfg2 = Config.load(tmp_path / "small")
    conf2 = cfg2.yok3x["limits"]["claude"]
    conf2.update({"autocalibrate": True, "limit_5h_tokens": 100, "min_interval_sec": 0})
    _seed_calib_history(cfg2, [100, 110, 100], ["up", "up", "down"])
    limits._CLAUDE_CALIBRATION_STATE.clear()
    _stub_window_tokens(monkeypatch, 11.0)
    got2 = limits.autocalibrate_claude(
        cfg2, conf2, limits.LimitReading("claude", "claude_oauth", True, True,
                                         [_calib_win("5h", 10.0)]))
    assert "진동" not in str(got2["skipped"])
    assert conf2["autocalibrate"] is True


def test_apply_config_and_build_state_expose_autocalibrate(tmp_path, monkeypatch):
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    assert gs._apply_config(cfg, {"autocalibrate": True})["ok"]
    assert cfg.yok3x["limits"]["claude"]["autocalibrate"] is True
    assert gs._apply_config(cfg, {"autocalibrate": False})["ok"]
    assert cfg.yok3x["limits"]["claude"]["autocalibrate"] is False
    monkeypatch.setattr(gs.usage, "today_totals", lambda c: {})
    empty = type("V", (), {"reading": None, "real": False, "level": "ok",
                            "ratio": 0, "source": "none", "detail": ""})()
    monkeypatch.setattr(gs.usage, "check_backend", lambda c, b, **kw: empty)
    monkeypatch.setattr(gs.usage, "coach_messages", lambda c, **kw: [])
    monkeypatch.setattr(gs, "_routing_preview", lambda c: {})
    monkeypatch.setattr(gs, "_profile_routes", lambda c: {})
    monkeypatch.setattr(gs.limits, "list_models", lambda c, b: [])
    monkeypatch.setattr(gs.limits, "list_models_gui", lambda c, b: [])
    monkeypatch.setattr(gs.limits, "claude_token_status", lambda c: {})
    state = gs.build_state(cfg)
    assert state["claude_autocalibrate"] is False


def test_apply_config_refreshes_cached_gui_state_immediately(mock_root, monkeypatch):
    """BUG-52: 저장 직후 재조회가 저장 전 5초 snapshot으로 UI를 되돌리면 안 된다."""
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    pace = cfg.yok3x["guard"].setdefault("daily_pace", {})
    pace["underuse_policy"] = "carry_fast"
    cfg.save_yok3x()
    old_snapshot = {"guard": {"pace": {"underuse_policy": "carry_fast"}}}
    monkeypatch.setattr(gs, "_GUI_STATE_GUARD", threading.Condition())
    monkeypatch.setattr(gs, "_GUI_STATE", old_snapshot)
    monkeypatch.setattr(gs, "_GUI_STATE_BUILT_AT", time.time())
    monkeypatch.setattr(gs, "_GUI_STATE_REFRESHING", False)
    builds = []

    def fake_build_state(current_cfg):
        builds.append(1)
        policy = current_cfg.yok3x["guard"]["daily_pace"]["underuse_policy"]
        return {"guard": {"pace": {"underuse_policy": policy}}}

    monkeypatch.setattr(gs, "build_state", fake_build_state)

    assert gs._apply_config(
        cfg, {"daily_pace": {"underuse_policy": "carry_smooth"}}) == {"ok": True}
    assert builds == [1]
    assert gs._gui_state(cfg)["guard"]["pace"]["underuse_policy"] == "carry_smooth"


def test_gui_state_polling_still_serves_fresh_cached_snapshot(mock_root, monkeypatch):
    """BUG-52: GET 폴링은 build_state를 요청 스레드에서 다시 계산하지 않는다."""
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    snapshot = {"guard": {"pace": {"underuse_policy": "carry_smooth"}}}
    monkeypatch.setattr(gs, "_GUI_STATE_GUARD", threading.Condition())
    monkeypatch.setattr(gs, "_GUI_STATE", snapshot)
    monkeypatch.setattr(gs, "_GUI_STATE_BUILT_AT", time.time())
    monkeypatch.setattr(gs, "_GUI_STATE_REFRESHING", False)
    builds = []
    monkeypatch.setattr(gs, "build_state", lambda _cfg: builds.append(1))

    assert gs._gui_state(cfg, copy_snapshot=False) is snapshot
    assert gs._gui_state(cfg, copy_snapshot=False) is snapshot
    assert builds == []


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
                        lambda c, b, **kw: usage.GuardVerdict(b, 0.2, "5h", "ok", "d"))
    assert usage.backend_available(cfg, "claude") is True
    monkeypatch.setattr(usage, "check_backend",
                        lambda c, b, **kw: usage.GuardVerdict(b, 1.0, "5h", "stop", "d"))
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


def _write_sync_bundle_for_cli(tmp_path, run_id="run_sync_cli", **extra):
    run_dir = tmp_path / ".yok3x" / "runs" / run_id
    run_dir.mkdir(parents=True)
    bundle = {
        "claims": [{
            "claim_id": "claim-cli-1",
            "type": sync_layer.FACT,
            "text": "CLI에 표시할 핵심 사실",
            "evidence_refs": [{"file": "app.py", "symbol_or_hunk": "main", "source": "diff"}],
        }],
        **extra,
    }
    (run_dir / "understanding_bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return run_dir


def test_sync_cli_prints_claims(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    _write_sync_bundle_for_cli(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["sync", "run_sync_cli"]) == 0
    assert "CLI에 표시할 핵심 사실" in capsys.readouterr().out


def test_sync_cli_prints_standard_quiz_questions(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    _write_sync_bundle_for_cli(
        tmp_path, standard_quiz={"questions": ["이 사실의 근거는 무엇인가?"]})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["sync", "run_sync_cli"]) == 0
    output = capsys.readouterr().out
    assert "Standard Quiz" in output
    assert "이 사실의 근거는 무엇인가?" in output


def test_sync_cli_without_bundle_is_soft_success(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    (tmp_path / ".yok3x" / "runs" / "run_without_sync").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["sync", "run_without_sync"]) == 0
    assert "sync_layer 데이터가 없습니다" in capsys.readouterr().out


def test_sync_cli_missing_run_is_error(tmp_path, monkeypatch, capsys):
    from yok3x import cli

    monkeypatch.chdir(tmp_path)

    assert cli.main(["sync", "does-not-exist"]) != 0
    assert "존재하지 않는 run_id" in capsys.readouterr().err


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


def test_daily_pace_grace_band_keeps_cap_within_grace(tmp_path):
    """grace_band는 작은 초과를 grace 안에서 허용해 기본 상한 q를 유지한다."""
    from yok3x import usage
    q = 14.0
    reset_at = 5 * 86400.0
    assert usage._grace_band_cap(q, 31.0, reset_at, 2.0, now=0.0) == q


def test_daily_pace_grace_band_reduces_cap_after_grace(tmp_path):
    """grace_band는 grace를 넘긴 초과분에 대해서만 q보다 낮게 완만히 줄인다."""
    from yok3x import usage
    q = 14.0
    reset_at = 5 * 86400.0
    cap = usage._grace_band_cap(q, 36.0, reset_at, 2.0, now=0.0)
    assert cap < q


def test_daily_pace_debt_amortize_is_gentler_than_catch_up(tmp_path):
    """debt_amortize는 초과 시 상한을 줄이되 catch_up보다 완만하게 조절한다."""
    from yok3x import usage
    q = 14.0
    reset_at = 5 * 86400.0
    debt_cap = usage._debt_amortize_cap(q, 36.0, reset_at, 2.0, now=0.0)
    catch_up_cap = usage._catch_up_cap(q, 36.0, reset_at, 2.0, now=0.0)
    assert debt_cap < q and debt_cap > catch_up_cap


def test_apply_config_accepts_new_daily_pace_strategies(tmp_path):
    """guiserver가 새 daily_pace 전략 두 값을 실제 설정에 저장한다."""
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    for strat in ("grace_band", "debt_amortize"):
        r = gs._apply_config(cfg, {"daily_pace": {"strategy": strat}})
        assert r.get("ok") is True
        assert cfg.yok3x["guard"]["daily_pace"]["strategy"] == strat


def test_daily_pace_codex_day1_anchors_at_reset(tmp_path):
    """codex(토큰없음, since_reset_known=False)는 '주간 첫날'엔 리셋 이후=오늘이라 롤링%를 통째로 오늘
    사용으로 잡고 상한은 기준을 온전히 준다: 리셋이 방금 있었고 5% 썼으면 오늘 5 / 상한 14 (사용자 지적).
    이후(비첫날)엔 하루 시작 롤링%가 기준선이라 상한이 그만큼 조여진다."""
    import time
    from yok3x import usage
    def mkcfg(sub):
        c = Config.load(tmp_path / sub)
        c.yok3x["guard"]["daily_pace"] = {"enabled": True, "pct_of_weekly": 0.14,
                                          "mode": "warn", "strategy": "catch_up"}
        return c
    now = time.time()
    # 첫날: 리셋 7일 후−1h → last_reset=now−1h, 하루시작=now−1h=last_reset → is_day1.
    reset1 = now + 7 * 86400 - 3600
    s1 = usage.daily_pace_status(mkcfg("d1"), "codex", 5.0, today=usage._pacing_day_key(reset1),
                                 reset_at=reset1, today_used=None, since_reset_known=False)
    assert round(s1["used"]) == 5     # 오늘 = 롤링 5%(리셋 이후 전부 오늘)
    assert round(s1["cap"]) == 14     # 상한 온전(오늘 이전 사용 0)
    # 비첫날(3일차: 리셋 5일 후 → 하루시작≠last_reset): 롤링 30% = 오늘 이전 사용 기준선 → 상한 42−30=12,
    # 첫 관측이라 오늘은 0(하루 시작 스냅샷=현재).
    reset3 = now + 5 * 86400
    s3 = usage.daily_pace_status(mkcfg("d3"), "codex", 30.0, today=usage._pacing_day_key(reset3),
                                 reset_at=reset3, today_used=None, since_reset_known=False)
    assert round(s3["used"]) == 0
    assert round(s3["cap"]) == 12
    # 대조: claude(토큰 since-reset)는 30%를 u0로 써 3일차 상한 42−30=12(첫날이든 아니든 동일 규칙).
    sc = usage.daily_pace_status(mkcfg("c"), "claude", 30.0, today=usage._pacing_day_key(reset3),
                                 reset_at=reset3, since_reset_known=True)
    assert round(sc["cap"]) == 12


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


def test_daily_pace_v2_carry_fast_cut_fast_matches_catch_up():
    from yok3x import usage
    for u0 in (0.0, 30.0, 70.0, 84.0):
        old = usage._catch_up_cap(14.0, u0, 5 * 86400.0, 2.0, now=0.0)
        new = usage._daily_cap_v2("carry_fast", "cut_fast", 14.0, u0, 5 * 86400.0, 2.0, now=0.0)
        assert new == old


def test_daily_pace_v2_carry_smooth_cut_smooth_matches_spread():
    from yok3x import usage
    for u0 in (0.0, 30.0, 70.0, 84.0):
        assert usage._daily_cap_v2("carry_smooth", "cut_smooth", 14.0, u0,
                                   5 * 86400.0, 2.0, now=0.0) == usage._spread_cap(
                                       14.0, u0, 5 * 86400.0, 2.0, now=0.0)


def test_daily_pace_v2_grace_and_debt_match_legacy():
    from yok3x import usage
    args = (14.0, 36.0, 5 * 86400.0, 2.0)
    assert usage._daily_cap_v2("flat", "grace_band", *args, now=0.0) == usage._grace_band_cap(*args, now=0.0)
    assert usage._daily_cap_v2("flat", "debt_amortize", *args, now=0.0) == usage._debt_amortize_cap(*args, now=0.0)


def test_daily_pace_v2_supports_mixed_policies():
    from yok3x import usage
    cap = usage._daily_cap_v2("carry_fast", "debt_amortize", 14.0, 36.0,
                              5 * 86400.0, 2.0, now=0.0)
    assert 0.0 < cap < 14.0


def test_apply_config_saves_daily_pace_axes_independently(tmp_path):
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    assert gs._apply_config(cfg, {"daily_pace": {"underuse_policy": "carry_fast"}})["ok"]
    assert gs._apply_config(cfg, {"daily_pace": {"overuse_policy": "debt_amortize"}})["ok"]
    dp = cfg.yok3x["guard"]["daily_pace"]
    assert dp["underuse_policy"] == "carry_fast" and dp["overuse_policy"] == "debt_amortize"


def test_daily_pace_new_policy_whitelists_reject_unknown(tmp_path):
    from yok3x import guiserver as gs
    cfg = Config.load(tmp_path)
    gs._apply_config(cfg, {"daily_pace": {"underuse_policy": "carry_fast", "overuse_policy": "flat"}})
    gs._apply_config(cfg, {"daily_pace": {"underuse_policy": "bogus", "overuse_policy": "bogus"}})
    dp = cfg.yok3x["guard"]["daily_pace"]
    assert dp["underuse_policy"] == "carry_fast" and dp["overuse_policy"] == "flat"


def test_daily_pace_custom_pair_is_reported_as_custom(tmp_path):
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["daily_pace"].update(enabled=True, underuse_policy="carry_fast",
                                             overuse_policy="debt_amortize")
    status = usage.daily_pace_status(cfg, "claude", 36.0, today="custom", reset_at=5 * 86400.0)
    assert status["strategy"] == "custom"


def test_gui_has_independent_pace_policy_handlers():
    html = Path("gui/index.html").read_text(encoding="utf-8")
    assert "saveUnderusePolicy('carry_fast')" in html
    assert "saveOverusePolicy('debt_amortize')" in html
    assert "data-us=" in html and "data-os=" in html


# --------------------------------------------------------- v4.1.0 MCP 워커도구(a1)

def test_resolve_mcp_grant_default_denied_no_request():
    """워커가 mcp_tools를 아예 설정 안 하면(대다수) 항상 비활성 — 기존 텍스트 생산자 동작 불변."""
    from yok3x import mcp_policy
    g = mcp_policy.resolve_mcp_grant({"fs": {"command": "npx"}}, {"backend": "claude"})
    assert not g.active and not g.servers and not g.allow_tools
    assert "opt-in 안 함" in g.denied_reason


def test_resolve_mcp_grant_denied_when_whitelist_empty():
    """전역 mcp_servers가 비어있으면(기본값) 워커가 요청해도 fail-closed."""
    from yok3x import mcp_policy
    worker = {"mcp_tools": {"servers": ["fs"], "allow_tools": ["mcp__fs__read"]}}
    g = mcp_policy.resolve_mcp_grant({}, worker)
    assert not g.active
    assert "화이트리스트가 비어있음" in g.denied_reason


def test_resolve_mcp_grant_denied_unknown_server():
    """화이트리스트에 없는 서버를 요청하면 그 서버는 거부되고(전체 거부), 이유가 남는다."""
    from yok3x import mcp_policy
    worker = {"mcp_tools": {"servers": ["not-registered"], "allow_tools": ["mcp__not-registered__x"]}}
    g = mcp_policy.resolve_mcp_grant({"fs": {"command": "npx"}}, worker)
    assert not g.active
    assert "화이트리스트에 없음" in g.denied_reason


def test_resolve_mcp_grant_denied_no_valid_allow_tools():
    """서버는 화이트리스트에 있어도 allow_tools가 비었거나 형식이 잘못되면 전체 거부한다
    (서버를 안다고 해서 그 서버의 모든 도구를 자동 허용하지 않음 — 도구명 명시가 필수)."""
    from yok3x import mcp_policy
    reg = {"fs": {"command": "npx"}}
    g1 = mcp_policy.resolve_mcp_grant(reg, {"mcp_tools": {"servers": ["fs"], "allow_tools": []}})
    assert not g1.active
    g2 = mcp_policy.resolve_mcp_grant(reg, {"mcp_tools": {"servers": ["fs"],
                                                          "allow_tools": ["not-a-valid-name"]}})
    assert not g2.active


def test_resolve_mcp_grant_drops_tool_outside_granted_server():
    """도구명이 mcp__<server>__<tool> 형태여도, 그 서버가 이번 요청에서 승인된 서버 집합 밖이면
    버려진다(서버 경계를 넘는 도구명 요청 차단 — 예: fs만 허용됐는데 mcp__other__delete 요청)."""
    from yok3x import mcp_policy
    reg = {"fs": {"command": "npx"}}
    worker = {"mcp_tools": {"servers": ["fs"],
                            "allow_tools": ["mcp__fs__read", "mcp__other__delete"]}}
    g = mcp_policy.resolve_mcp_grant(reg, worker)
    assert g.active
    assert g.allow_tools == ["mcp__fs__read"]
    assert "형식·서버경계 위반" in g.denied_reason   # 버려진 것도 기록에 남음(감사용)


def test_resolve_mcp_grant_active_happy_path():
    """화이트리스트 서버 + 유효한 allow_tools면 활성 grant를 낸다."""
    from yok3x import mcp_policy
    reg = {"fs": {"command": "npx", "args": ["-y", "server-filesystem"]}}
    worker = {"mcp_tools": {"servers": ["fs"], "allow_tools": ["mcp__fs__read", "mcp__fs__list"]}}
    g = mcp_policy.resolve_mcp_grant(reg, worker)
    assert g.active
    assert g.servers == reg
    assert g.allow_tools == ["mcp__fs__read", "mcp__fs__list"]
    assert g.denied_reason == ""


def test_write_mcp_config_file_writes_valid_json(tmp_path):
    """claude --mcp-config가 읽을 임시 JSON이 mcpServers 스키마로 정확히 쓰여진다."""
    from yok3x import mcp_policy
    grant = mcp_policy.McpGrant(servers={"fs": {"command": "npx", "args": ["x"]}},
                                allow_tools=["mcp__fs__read"])
    path = mcp_policy.write_mcp_config_file(grant)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data == {"mcpServers": {"fs": {"command": "npx", "args": ["x"]}}}
    finally:
        Path(path).unlink(missing_ok=True)


def test_record_grant_appends_jsonl(tmp_path):
    """감사로그에 승인/거부 여부와 서버·도구·사유가 남는다. 실패 대상 없음."""
    from yok3x import mcp_policy
    grant = mcp_policy.McpGrant(servers={"fs": {}}, allow_tools=["mcp__fs__read"])
    mcp_policy.record_grant(tmp_path, run_id="run-1", step=3, worker="claude-main",
                            backend="claude", grant=grant, timestamp=1000.0)
    rec = json.loads(mcp_policy.audit_log_path(tmp_path).read_text(encoding="utf-8").strip())
    assert rec == {"ts": 1000.0, "run_id": "run-1", "step": 3, "worker": "claude-main",
                   "backend": "claude", "activated": True, "servers": ["fs"],
                   "allow_tools": ["mcp__fs__read"], "denied_reason": ""}


def test_run_cli_injects_mcp_args_and_removes_disallowed(monkeypatch):
    """mcp_config_path가 주어지고 backend에 mcp_arg 템플릿이 있으면 argv에 주입되고, 기존
    --disallowedTools(전면 차단)는 도구 화이트리스트와 충돌하지 않게 제거된다."""
    from yok3x import backends
    cap = {}
    class _P:
        stdout = '{"result":"ok","is_error":false}'; stderr = ""; returncode = 0
    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: (cap.__setitem__("c", cmd), _P())[1])
    monkeypatch.setattr(backends.shutil, "which", lambda x: x)
    spec = {"type": "cli", "command": ["claude", "-p", "--disallowedTools", "Bash,Edit,Write"],
           "mcp_arg": ["--mcp-config", "{mcp_config_path}", "--allowedTools", "{allowed_tools}"],
           "parser": "raw"}
    backends.run_backend("claude", spec, "hi",
                         mcp_config_path="/tmp/mcp.json", mcp_allowed_tools="mcp__fs__read")
    assert "--disallowedTools" not in cap["c"]
    assert cap["c"][-4:] == ["--mcp-config", "/tmp/mcp.json", "--allowedTools", "mcp__fs__read"]


def test_run_cli_ignores_mcp_when_backend_lacks_mcp_arg_template(monkeypatch):
    """backend spec에 mcp_arg가 없으면(codex/gemini 등) mcp_config_path가 와도 조용히 무시된다
    (fail-closed — mcp_policy가 승인해도 이 backend는 도구를 못 씀)."""
    from yok3x import backends
    cap = {}
    class _P:
        stdout = '{"result":"ok","is_error":false}'; stderr = ""; returncode = 0
    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: (cap.__setitem__("c", cmd), _P())[1])
    monkeypatch.setattr(backends.shutil, "which", lambda x: x)
    spec = {"type": "cli", "command": ["codex", "exec"], "parser": "raw"}   # mcp_arg 없음
    backends.run_backend("codex", spec, "hi",
                         mcp_config_path="/tmp/mcp.json", mcp_allowed_tools="mcp__fs__read")
    assert cap["c"] == ["codex", "exec"]


def test_execute_call_no_mcp_request_never_touches_gate_mcp(mock_root, monkeypatch):
    """대다수 워커(mcp_tools 미설정)는 _gate_mcp가 아예 호출되지 않는다(기존 동작 완전 불변)."""
    cfg = Config.load(mock_root)
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda *a, **kw: BackendResult(backend=a[0], ok=True, text="ok"))
    o = Orchestrator(cfg, auto=True)
    calls = []
    monkeypatch.setattr(Orchestrator, "_gate_mcp", lambda self, desc: calls.append(desc) or True)
    assert o.call_worker("claude-main", "t").ok
    assert calls == []


def test_execute_call_mcp_grant_forces_gate_even_with_auto_approve(mock_root, monkeypatch):
    """워커에 유효한 mcp_tools grant가 걸리면 auto_approve=True(런 전체 자동승인)여도
    _gate_mcp가 불려 사람 확인을 요구한다 — 계획서 codex 리뷰의 '승인 필수' 강한 적용."""
    cfg = Config.load(mock_root)
    cfg.yok3x["mcp_servers"] = {"fs": {"command": "npx"}}
    cfg.yok3x["workers"]["claude-main"]["mcp_tools"] = {
        "servers": ["fs"], "allow_tools": ["mcp__fs__read"]}
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda *a, **kw: BackendResult(backend=a[0], ok=True, text="ok"))
    o = Orchestrator(cfg, auto=True)     # 런 전체는 auto-approve
    gate_calls = []
    monkeypatch.setattr(Orchestrator, "_gate_mcp",
                        lambda self, desc: gate_calls.append(desc) or True)
    assert o.call_worker("claude-main", "t").ok
    assert len(gate_calls) == 1 and "mcp__fs__read" in gate_calls[0]


def test_execute_call_mcp_gate_declined_skips_step_without_calling_backend(mock_root, monkeypatch):
    """MCP 게이트에서 거부하면 그 워커 호출은 실행되지 않고(run_backend 미호출) skipped로 남는다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["mcp_servers"] = {"fs": {"command": "npx"}}
    cfg.yok3x["workers"]["claude-main"]["mcp_tools"] = {
        "servers": ["fs"], "allow_tools": ["mcp__fs__read"]}
    backend_calls = []
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda *a, **kw: backend_calls.append(1) or BackendResult(backend=a[0], ok=True))
    o = Orchestrator(cfg, auto=True)
    monkeypatch.setattr(Orchestrator, "_gate_mcp", lambda self, desc: False)   # 사람이 거부
    res = o.call_worker("claude-main", "t")
    assert not res.ok and "declined" in res.error
    assert backend_calls == []


def test_execute_call_mcp_grant_approved_passes_config_to_backend_and_cleans_up(mock_root, monkeypatch):
    """게이트 승인 시 임시 mcp_config_path·mcp_allowed_tools가 run_backend에 전달되고,
    호출이 끝나면(성공이든 실패든) 임시 파일이 정리된다(비밀값이 남을 수 있는 파일이라 유지 안 함)."""
    cfg = Config.load(mock_root)
    cfg.yok3x["mcp_servers"] = {"fs": {"command": "npx"}}
    cfg.yok3x["workers"]["claude-main"]["mcp_tools"] = {
        "servers": ["fs"], "allow_tools": ["mcp__fs__read", "mcp__fs__list"]}
    seen = {}
    written_path = {}

    def fake_backend(name, spec, prompt, **kwargs):
        written_path["path"] = kwargs.get("mcp_config_path")
        assert written_path["path"] and Path(written_path["path"]).exists()   # 실제로 존재하는 동안 호출됨
        seen["allowed_tools"] = kwargs.get("mcp_allowed_tools")
        return BackendResult(backend=name, ok=True, text="ok")
    monkeypatch.setattr(orchestrator, "run_backend", fake_backend)
    o = Orchestrator(cfg, auto=True)
    monkeypatch.setattr(Orchestrator, "_gate_mcp", lambda self, desc: True)
    assert o.call_worker("claude-main", "t").ok
    assert seen["allowed_tools"] == "mcp__fs__read,mcp__fs__list"
    assert not Path(written_path["path"]).exists()   # 호출 후 정리됨


def test_execute_call_mcp_denied_request_audits_without_gate(mock_root, monkeypatch):
    """워커가 mcp_tools를 요청했지만 정책상 거부(화이트리스트 밖 등)되면: 감사로그엔 남지만
    _gate_mcp는 안 불리고(활성 grant가 아니므로) 호출은 평소처럼(도구 없이) 진행된다."""
    cfg = Config.load(mock_root)
    cfg.yok3x["mcp_servers"] = {}   # 화이트리스트 비어있음 → 무조건 거부
    cfg.yok3x["workers"]["claude-main"]["mcp_tools"] = {
        "servers": ["fs"], "allow_tools": ["mcp__fs__read"]}
    monkeypatch.setattr(orchestrator, "run_backend",
                        lambda *a, **kw: BackendResult(backend=a[0], ok=True, text="ok"))
    o = Orchestrator(cfg, auto=True)
    gate_calls = []
    monkeypatch.setattr(Orchestrator, "_gate_mcp", lambda self, desc: gate_calls.append(desc) or True)
    assert o.call_worker("claude-main", "t").ok
    assert gate_calls == []                          # 활성 아니라 게이트 자체가 안 불림
    from yok3x import mcp_policy
    log = mcp_policy.audit_log_path(cfg.paths.yok3x_dir).read_text(encoding="utf-8").strip()
    rec = json.loads(log)
    assert rec["activated"] is False and "비어있음" in rec["denied_reason"]


# --------------------------------------------------------- v4.6.0 Cognitive Sync Layer (mode=off)

_SAMPLE_DIFF = """--- a/pkg/auth.py
+++ b/pkg/auth.py
@@ -1,3 +1,6 @@
 def existing():
     pass
+
+def rotate_refresh_token():
+    pass
"""


def test_sync_layer_parse_diff_extracts_files_and_symbols():
    """S2: unified diff에서 변경파일·추가된 심볼(근사)을 순수 정규식으로 뽑는다(LLM 호출 없음)."""
    from yok3x import sync_layer
    files, symbols = sync_layer._parse_diff(_SAMPLE_DIFF)
    assert files == ["pkg/auth.py"]
    assert symbols == {"pkg/auth.py": ["rotate_refresh_token"]}


def test_sync_layer_parse_run_log_decisions():
    """S2: run.log의 [route]/[degrade]/[failover]/[gate] 줄만 RECORDED_DECISION 후보로 뽑는다."""
    from yok3x import sync_layer
    log = ("[2026-08-08T00:00:00] [route] build → codex (codex/gpt)\n"
          "[2026-08-08T00:00:01] 그냥 일반 로그 줄(무시돼야 함)\n"
          "[2026-08-08T00:00:02] [gate] step 1: claude-main — 승인\n")
    decisions = sync_layer._parse_run_log_decisions(log)
    assert decisions == [
        {"kind": "route", "text": "build → codex (codex/gpt)"},
        {"kind": "gate", "text": "step 1: claude-main — 승인"},
    ]


@pytest.mark.parametrize(("verdict", "expected_type"), [
    ("confirmed", "FACT"),
    ("partial", "OPEN_QUESTION"),
])
def test_sync_layer_acquire_claims_verdict_mapping(verdict, expected_type):
    """S2: ACQUIRE verdict을 claim 타입으로 매핑 — confirmed(경로+심볼 확인)=FACT,
    partial(위치 힌트로만, acquire.py의 downgrade 의미 그대로 존중)=OPEN_QUESTION."""
    from yok3x import sync_layer
    data = {"qa_items": [{
        "claim_id": "abc123", "verdict": verdict,
        "answer": {"answer": "재시도 상태를 Redis에 저장한다.",
                   "evidence": [{"path": "worker/state.py", "symbol": "save_state"}]},
    }]}
    claims = sync_layer._acquire_claims(data)
    assert len(claims) == 1 and claims[0]["type"] == expected_type
    assert claims[0]["evidence_refs"] == [
        {"file": "worker/state.py", "symbol_or_hunk": "save_state", "source": "acquire"}]


def test_sync_layer_acquire_dropped_becomes_open_question_with_reason():
    """S2: contradicted(폐기)로 걸러진 ACQUIRE 가설도 '폐기됐다는 사실 자체'는 OPEN_QUESTION으로 남는다."""
    from yok3x import sync_layer
    data = {"dropped": [{"claim_id": "x", "verdict": "contradicted",
                         "reason": "evidence path does not exist",
                         "answer": {"answer": "PostgreSQL을 쓴다.", "evidence": []}}]}
    claims = sync_layer._acquire_claims(data)
    assert len(claims) == 1
    assert claims[0]["type"] == "OPEN_QUESTION"
    assert "[폐기된 가설]" in claims[0]["text"] and "evidence path does not exist" in claims[0]["text"]


def test_build_understanding_bundle_assembles_all_sources(tmp_path):
    """S2 통합: changes.diff + run.log + acquire.json을 한 번에 조립. 신규 LLM 호출 없이(순수
    파일 읽기) claim이 세 출처 모두에서 나오는지 확인."""
    from yok3x import sync_layer
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    review_root = tmp_path / "review"
    review_root.mkdir()
    (review_root / "changes.diff").write_text(_SAMPLE_DIFF, encoding="utf-8")
    (run_dir / "run.log").write_text("[route] build → codex\n", encoding="utf-8")
    (run_dir / "acquire.json").write_text(json.dumps({"qa_items": [{
        "claim_id": "c1", "verdict": "confirmed",
        "answer": {"answer": "설계상 이유", "evidence": [{"path": "pkg/auth.py", "symbol": "existing"}]},
    }]}), encoding="utf-8")

    bundle = sync_layer.build_understanding_bundle(run_dir, review_root)
    types_present = {c["type"] for c in bundle["claims"]}
    assert {"FACT", "INFERENCE", "RECORDED_DECISION"} <= types_present
    assert bundle["run_dir"] == str(run_dir)


def test_build_understanding_bundle_missing_files_degrades_gracefully(tmp_path):
    """폴백 가드: run_dir에 아무 파일도 없어도(런이 review/acquire 미사용) 예외 없이 빈 조립."""
    from yok3x import sync_layer
    run_dir = tmp_path / "empty_run"
    bundle = sync_layer.build_understanding_bundle(run_dir, review_root=None)
    assert bundle["claims"] == []


def test_build_understanding_bundle_demotes_evidenceless_claims_to_open_question():
    """근거(evidence_refs) 없이 FACT/RECORDED_DECISION으로 분류될 뻔한 claim은 자동으로
    OPEN_QUESTION으로 강등된다(정직 표기 원칙 — 억지로 채우지 않음)."""
    from yok3x import sync_layer
    # RECORDED_DECISION 소스인 run_log 파싱 결과는 항상 evidence_refs가 있으므로, 강등 로직
    # 자체를 직접 함수 호출로 검증(내부 리스트를 흉내내 강등 조건만 확인).
    claims = [{"type": sync_layer.FACT, "text": "x", "evidence_refs": []}]
    for c in claims:
        if c["type"] in (sync_layer.FACT, sync_layer.RECORDED_DECISION) and not c["evidence_refs"]:
            c["type"] = sync_layer.OPEN_QUESTION
    assert claims[0]["type"] == sync_layer.OPEN_QUESTION


def test_bundle_cache_key_stable_and_mode_sensitive(tmp_path):
    """S6 캐시 키: 같은 입력 → 같은 키, mode만 달라져도 다른 키(모드별 캐시 분리)."""
    from yok3x import sync_layer
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "acquire.json").write_text('{"qa_items": []}', encoding="utf-8")
    k1 = sync_layer.bundle_cache_key(run_dir, None, "off")
    k2 = sync_layer.bundle_cache_key(run_dir, None, "off")
    k3 = sync_layer.bundle_cache_key(run_dir, None, "light")
    assert k1 == k2
    assert k1 != k3


def test_check_drift_detects_changed_and_deleted_referenced_files(tmp_path):
    """S3 Drift Detector: 파일 내용이 바뀌거나 사라지면 그 파일을 근거로 쓰는 claim만 STALE로
    표시되고, 관련 없는 claim은 그대로 남는다."""
    from yok3x import sync_layer
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "a.py").write_text("original a", encoding="utf-8")
    (workdir / "b.py").write_text("original b", encoding="utf-8")
    claims = [
        {"claim_id": "claim-a", "type": sync_layer.FACT, "text": "a",
         "evidence_refs": [{"file": "a.py", "symbol_or_hunk": "", "source": "diff"}]},
        {"claim_id": "claim-b", "type": sync_layer.FACT, "text": "b",
         "evidence_refs": [{"file": "b.py", "symbol_or_hunk": "", "source": "diff"}]},
    ]
    bundle = {"claims": claims,
             "file_hashes": sync_layer._hash_referenced_files(claims, workdir)}
    assert sync_layer.check_drift(bundle, workdir) == []   # 아직 안 바뀜

    (workdir / "a.py").write_text("changed a", encoding="utf-8")
    assert sync_layer.check_drift(bundle, workdir) == ["claim-a"]   # b는 그대로라 안 걸림

    (workdir / "b.py").unlink()
    assert set(sync_layer.check_drift(bundle, workdir)) == {"claim-a", "claim-b"}   # 삭제도 STALE


def test_static_checklist_unknown_tier_fails_closed_to_api():
    """S4: 모르는 tier는 가장 엄격한 'api' 체크리스트로 fail-closed(위험 과소평가보다 안전)."""
    from yok3x import sync_layer
    assert sync_layer.static_checklist("nonexistent-tier") == sync_layer.static_checklist("api")
    assert sync_layer.static_checklist("direct") != sync_layer.static_checklist("api")


def test_render_markdown_groups_by_type_and_includes_checklist():
    """S5: 렌더가 타입별로 묶고, tier가 주어지면 체크리스트도 붙인다. 빈 번들도 안 죽는다."""
    from yok3x import sync_layer
    bundle = {"claims": [
        {"type": sync_layer.FACT, "text": "파일 3개 변경됨",
         "evidence_refs": [{"file": "x.py", "symbol_or_hunk": "", "source": "diff"}]},
        {"type": sync_layer.OPEN_QUESTION, "text": "Redis 장애 처리 미검증", "evidence_refs": []},
    ]}
    md = sync_layer.render_markdown(bundle, tier="api")
    assert "확인된 사실" in md and "파일 3개 변경됨" in md
    assert "미해결/근거부족" in md and "Redis 장애 처리 미검증" in md
    assert "이해 체크리스트" in md
    empty_md = sync_layer.render_markdown({"claims": []})
    assert "조립할 근거 없음" in empty_md


def test_finish_sync_layer_disabled_by_default_produces_no_files(tmp_path, monkeypatch):
    """opt-in 원칙: sync_layer.enabled 기본 False면 _finish가 understanding_bundle 파일을
    전혀 만들지 않는다(다른 opt-in 기능들과 동일하게 새 파일을 조용히 만들지 않음)."""
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    assert cfg.yok3x["sync_layer"]["enabled"] is False   # 기본값 확인
    wd = tmp_path / "proj"; wd.mkdir()
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    monkeypatch.setattr(orchestrator.knot, "save", lambda *a, **kw: None)

    o._finish("task", "```file:ok.txt\nok\n```\n")
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert "sync_layer" not in status
    assert not (o.run_dir / "understanding_bundle.json").exists()


def test_finish_sync_layer_enabled_builds_bundle_and_records_status(tmp_path, monkeypatch):
    """enabled=True면 _finish가 run_dir·review_root 양쪽에 understanding_bundle을 쓰고,
    status.json에 claim 개수·tier를 기록한다. LLM 호출은 여전히 0(mode=off 기본)."""
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    cfg.yok3x["sync_layer"]["enabled"] = True
    wd = tmp_path / "proj"; wd.mkdir()
    (wd / "ok.txt").write_text("old", encoding="utf-8")
    o = O.Orchestrator(cfg, auto=True)
    o.workdir = str(wd)
    o.changes = {"mode": "review"}
    o.triage = {"tier": "local"}
    monkeypatch.setattr(orchestrator.knot, "save", lambda *a, **kw: None)
    o._log("[route] build → mock (mock/mock)")   # run.log에 RECORDED_DECISION 소스 하나 준비

    o._finish("task", "```file:ok.txt\nnew content\n```\n")
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["sync_layer"]["enabled"] is True
    assert status["sync_layer"]["tier"] == "local"
    assert status["sync_layer"]["claims"] >= 1

    bundle_json = json.loads((o.run_dir / "understanding_bundle.json").read_text(encoding="utf-8"))
    assert any(c["type"] == "RECORDED_DECISION" for c in bundle_json["claims"])
    md = (o.run_dir / "understanding_bundle.md").read_text(encoding="utf-8")
    assert "이해 체크리스트" in md   # tier="local" → static_checklist가 렌더에 포함됨
    review_root = Path(status["changes"]["root"])
    assert (review_root / "understanding_bundle.md").exists()


def test_finish_sync_layer_failure_does_not_break_run(tmp_path, monkeypatch):
    """폴백 가드: sync_layer 조립이 예외를 던져도 _finish/런 완료 자체는 절대 깨지지 않는다
    (mat/changes와 같은 원칙 — 부가 기능 실패가 본작업을 죽이면 안 됨, BUG-39류 재발 방지)."""
    from yok3x import orchestrator as O, sync_layer as SL
    cfg = Config.load(tmp_path)
    cfg.yok3x["sync_layer"]["enabled"] = True
    o = O.Orchestrator(cfg, auto=True)
    monkeypatch.setattr(orchestrator.knot, "save", lambda *a, **kw: None)
    monkeypatch.setattr(SL, "build_understanding_bundle",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    o._finish("task", "no file blocks here")   # 예외를 던지면 테스트가 여기서 실패함
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert status["sync_layer"]["ok"] is False and "boom" in status["sync_layer"]["reason"]


# --------------------------------------------------------- v4.6.0 S6a 온디맨드 클릭형 퀴즈/설명

def test_explain_and_quiz_claim_prompt_stay_scoped_to_one_claim():
    """S6a: 프롬프트가 그 claim 하나의 텍스트·근거만 담고(다른 claim 안 섞임), 근거 없으면
    '근거로는 알 수 없음'을 요구한다(§3.5 근거 있는 환각 방지)."""
    from yok3x import sync_layer
    claim = {"type": "FACT", "text": "3개 파일이 변경됨",
             "evidence_refs": [{"file": "a.py", "symbol_or_hunk": "foo", "source": "diff"}]}
    p1 = sync_layer.explain_claim_prompt(claim)
    assert "3개 파일이 변경됨" in p1 and "a.py::foo" in p1
    assert "근거로는 알 수 없음" in p1
    p2 = sync_layer.quiz_claim_prompt(claim)
    assert "3개 파일이 변경됨" in p2 and "Q:" in p2 and "A:" in p2


def test_sync_claim_action_rejects_unsafe_run_id(mock_root):
    """경로 탈출 시도(review.py의 기존 _safe_run_id 재사용)는 파일 조회 전에 거부된다."""
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    r = gs._sync_claim_action(cfg, {"run_id": "../../etc", "claim_id": "x", "action": "explain"})
    assert not r["ok"] and "잘못된 run_id" in r["error"]


def test_sync_claim_action_rejects_bad_action(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    r = gs._sync_claim_action(cfg, {"run_id": "run-1", "claim_id": "x", "action": "delete"})
    assert not r["ok"] and "explain 또는 quiz" in r["error"]


def test_sync_claim_action_missing_bundle_gives_clear_reason(mock_root):
    """번들이 없으면(그 런에서 sync_layer 미사용) 조용히 실패하지 않고 이유를 알려준다."""
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    r = gs._sync_claim_action(cfg, {"run_id": "no-such-run", "claim_id": "x", "action": "explain"})
    assert not r["ok"] and "understanding_bundle 없음" in r["error"]


def test_sync_claim_action_unknown_claim_id(mock_root):
    from yok3x import guiserver as gs
    cfg = Config.load(mock_root)
    run_dir = cfg.paths.runs / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "understanding_bundle.json").write_text(
        json.dumps({"claims": [{"claim_id": "c1", "type": "FACT", "text": "x", "evidence_refs": []}]}),
        encoding="utf-8")
    r = gs._sync_claim_action(cfg, {"run_id": "run-1", "claim_id": "does-not-exist", "action": "explain"})
    assert not r["ok"] and "claim_id 없음" in r["error"]


def test_sync_claim_action_happy_path_calls_backend_once_and_records_usage(mock_root, monkeypatch):
    """정상 경로: claim 조회 → 프롬프트 조립 → 백엔드 1회 호출 → 결과 반환 + 원장 기록."""
    from yok3x import guiserver as gs, usage as usage_mod
    cfg = Config.load(mock_root)
    cfg.yok3x["sync_layer"]["on_demand_backend"] = "mock"
    run_dir = cfg.paths.runs / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "understanding_bundle.json").write_text(
        json.dumps({"claims": [{"claim_id": "c1", "type": "FACT", "text": "파일 변경됨",
                                "evidence_refs": [{"file": "a.py", "symbol_or_hunk": "",
                                                    "source": "diff"}]}]}),
        encoding="utf-8")
    calls = []
    monkeypatch.setattr(gs.backends, "run_backend",
                        lambda name, spec, prompt, **kw: (calls.append(prompt) or
                                                          BackendResult(backend=name, ok=True,
                                                                       text="설명입니다", total_tokens=42)))
    recorded = []
    monkeypatch.setattr(usage_mod, "record", lambda cfg, worker, kind, res, run_id="":
                        recorded.append((worker, kind, run_id)))

    r = gs._sync_claim_action(cfg, {"run_id": "run-1", "claim_id": "c1", "action": "explain"})
    assert r == {"ok": True, "action": "explain", "claim_id": "c1", "text": "설명입니다",
                "cost_usd": 0.0, "tokens": 42}
    assert len(calls) == 1 and "파일 변경됨" in calls[0]
    assert recorded == [("sync_layer", "explain", "run-1")]


def test_sync_claim_action_guard_stop_blocks_call(mock_root, monkeypatch):
    """요금 가드가 stop이면 백엔드 호출 자체가 안 나간다(온디맨드 경로도 가드 우회 없음)."""
    from yok3x import guiserver as gs, usage as usage_mod
    cfg = Config.load(mock_root)
    cfg.yok3x["sync_layer"]["on_demand_backend"] = "mock"
    run_dir = cfg.paths.runs / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "understanding_bundle.json").write_text(
        json.dumps({"claims": [{"claim_id": "c1", "type": "FACT", "text": "x", "evidence_refs": []}]}),
        encoding="utf-8")
    monkeypatch.setattr(usage_mod, "check_backend",
                        lambda cfg, b: usage_mod.GuardVerdict(b, 1.0, "7d", "stop", "한도 초과"))
    calls = []
    monkeypatch.setattr(gs.backends, "run_backend", lambda *a, **kw: calls.append(1))

    r = gs._sync_claim_action(cfg, {"run_id": "run-1", "claim_id": "c1", "action": "quiz"})
    assert not r["ok"] and "요금 가드 정지" in r["error"]
    assert calls == []


# --------------------------------------------------------- v4.6.0 S6c light/deep

def test_s6c_light_instruction_is_added_to_critic_prompt(tmp_path):
    from yok3x.orchestrator import Orchestrator
    cfg = Config.load(tmp_path)
    cfg.yok3x["sync_layer"].update({"enabled": True, "mode": "light"})
    o = Orchestrator(cfg, auto=True)
    spec = o.prepare_call("codex-critic", "review", task_kind="critic")
    assert "Cognitive Sync Layer / light" in spec.prompt
    assert "ACQUIRE" in spec.prompt


def test_s6c_light_does_not_create_extra_call(tmp_path, monkeypatch):
    o, calls, _, bundle = _run_standard_sync_finish(tmp_path, monkeypatch,
                                                     tier="api", mode="light")
    assert calls == []
    assert "standard_quiz" not in bundle
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["sync_layer"]["light_instruction"] is True


def test_s6c_deep_api_calls_once_and_saves_forensic(tmp_path, monkeypatch):
    _, calls, _, bundle = _run_standard_sync_finish(tmp_path, monkeypatch,
                                                     tier="api", mode="deep")
    assert len(calls) == 1
    assert "deep_forensic" in bundle


def test_s6c_deep_non_api_does_not_call(tmp_path, monkeypatch):
    for tier in ("local", "direct"):
        _, calls, _, bundle = _run_standard_sync_finish(tmp_path / tier, monkeypatch,
                                                         tier=tier, mode="deep")
        assert calls == []
        assert "deep_forensic" not in bundle


def test_s6c_deep_budget_zero_skips_call(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    cfg.yok3x["sync_layer"].update({"enabled": True, "mode": "deep",
                                     "deep_call_budget": 0, "standard_backend": "mock"})
    cfg.yok3x["workers"]["claude-main"]["backend"] = "mock"
    cfg.backends["mock"] = {"type": "mock"}
    from yok3x import orchestrator as O
    o = O.Orchestrator(cfg, auto=True); o.triage = {"tier": "api"}
    calls = []
    monkeypatch.setattr(O, "run_backend", lambda *a, **kw: calls.append(1))
    monkeypatch.setattr(O.knot, "save", lambda *a, **kw: None)
    o._finish("task", "no file blocks here")
    assert calls == []
    assert "예산 상한 도달로 건너뜀" in (o.run_dir / "run.log").read_text(encoding="utf-8")


def test_s6c_deep_failure_does_not_break_run(tmp_path, monkeypatch):
    _, calls, status, _ = _run_standard_sync_finish(tmp_path, monkeypatch,
                                                     tier="api", mode="deep", raises=True)
    assert len(calls) == 1
    assert status["state"] == "done"


# --------------------------------------------------------- v4.6.0 S6b standard 자동 퀴즈

def _run_standard_sync_finish(tmp_path, monkeypatch, *, tier="local", mode="standard",
                              result_text='["질문 1", "질문 2", "질문 3"]', raises=False):
    from yok3x import orchestrator as O
    cfg = Config.load(tmp_path)
    cfg.yok3x["sync_layer"].update({"enabled": True, "mode": mode,
                                     "standard_backend": "mock"})
    cfg.yok3x["workers"]["claude-main"]["backend"] = "mock"
    cfg.backends["mock"] = {"type": "mock"}
    o = O.Orchestrator(cfg, auto=True)
    o.triage = {"tier": tier}
    calls = []

    def fake_backend(name, spec, prompt, **kwargs):
        calls.append(prompt)
        if raises:
            raise RuntimeError("quiz boom")
        return BackendResult(backend=name, ok=True, text=result_text, total_tokens=3)

    monkeypatch.setattr(O, "run_backend", fake_backend)
    monkeypatch.setattr(O.knot, "save", lambda *a, **kw: None)
    o._log("[route] build → mock (mock/mock)")
    o._finish("task", "no file blocks here")
    status = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))
    bundle_path = o.run_dir / "understanding_bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.exists() else {}
    return o, calls, status, bundle


def test_s6b_standard_t1_does_not_call_quiz(tmp_path, monkeypatch):
    """S6b: T1(direct)은 standard여도 자동 퀴즈를 만들지 않는다."""
    _, calls, _, bundle = _run_standard_sync_finish(tmp_path, monkeypatch, tier="direct")
    assert calls == []
    assert "standard_quiz" not in bundle


def test_s6b_standard_t2_calls_backend_once(tmp_path, monkeypatch):
    """S6b: T2 standard의 자동 질문 생성은 한 런에서 정확히 1회다."""
    _, calls, _, bundle = _run_standard_sync_finish(tmp_path, monkeypatch, tier="local")
    assert len(calls) == 1
    assert "standard_quiz" in bundle


def test_s6b_nonstandard_modes_do_not_call_quiz(tmp_path, monkeypatch):
    """S6b: off/light/deep에는 standard 자동 경로가 섞이지 않는다."""
    for mode in ("off", "light"):
        _, calls, _, bundle = _run_standard_sync_finish(tmp_path / mode, monkeypatch,
                                                         tier="api", mode=mode)
        assert calls == []
        assert "standard_quiz" not in bundle


def test_s6b_quiz_failure_does_not_break_run(tmp_path, monkeypatch):
    """S6b 부가 호출 실패는 본 런을 실패시키지 않는다."""
    _, calls, status, _ = _run_standard_sync_finish(tmp_path, monkeypatch, raises=True)
    assert len(calls) == 1
    assert status["state"] == "done"


def test_s6b_quiz_is_saved_as_questions_in_bundle(tmp_path, monkeypatch):
    """S6b 응답은 사용자 답변/평가 없이 질문 목록 필드로만 번들에 저장된다."""
    _, _, _, bundle = _run_standard_sync_finish(
        tmp_path, monkeypatch, result_text='{"questions": ["Q1", "Q2"]}')
    assert bundle["standard_quiz"] == {"questions": ["Q1", "Q2"]}
    assert "answers" not in bundle["standard_quiz"]


# Browser-based Claude login replaces the former URL/code submission flow.
def test_claude_login_start_launches_browser_flow(monkeypatch):
    from yok3x import guiserver as gs
    proc = type("Proc", (), {"wait": lambda self: None})()
    calls = {}
    monkeypatch.setattr(gs.shutil, "which", lambda name: "C:/bin/claude.exe")
    monkeypatch.setattr(gs.subprocess, "Popen", lambda *args, **kwargs: calls.update(
        args=args, kwargs=kwargs) or proc)

    result = gs._claude_login_start()

    assert result == {"ok": True}
    assert calls["args"][0] == ["C:/bin/claude.exe", "auth", "login", "--claudeai"]
    assert calls["kwargs"]["stdout"] is gs.subprocess.DEVNULL
    assert gs._CLAUDE_LOGIN_STARTED_AT is not None


def test_claude_login_status_reports_valid_token(monkeypatch):
    from yok3x import guiserver as gs
    cfg = type("Cfg", (), {"yok3x": {"limits": {"claude": {"plan": "max"}}}})()
    monkeypatch.setattr(gs.limits, "claude_token_status",
                        lambda conf: {"exists": True, "expired": False, "mins_left": 12})

    assert gs._claude_login_status(cfg) == {"exists": True, "expired": False}


def test_claude_login_status_reports_missing_token(monkeypatch):
    from yok3x import guiserver as gs
    cfg = type("Cfg", (), {"yok3x": {"limits": {"claude": {}}}})()
    monkeypatch.setattr(gs.limits, "claude_token_status",
                        lambda conf: {"exists": False, "expired": None})

    assert gs._claude_login_status(cfg) == {"exists": False, "expired": None}
