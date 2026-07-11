"""yok3x 회귀 잠금 테스트.

손으로 매번 확인하던 것을 자동화한다(RULE §5: 실행해서 확인). mock 백엔드 위에서
외부 도구 없이 결정적으로 돈다. 실행: 프로젝트 루트에서 `pytest` 또는 `python -m pytest`.
verify_cmd 게이트에 `pytest -q`를 걸면 프로젝트가 자기 자신을 dogfooding하게 된다.
"""
from __future__ import annotations

import json

import subprocess

import pytest

from yok3x import __version__, backends, limits, matview, usage
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
    def __init__(self, payload): self._p = payload
    def read(self): return self._p
    def __enter__(self): return self
    def __exit__(self, *a): return False


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
    conf = {"type": "claude_oauth", "min_interval_sec": 0,
            "credentials_path": str(tmp_path / "nope.json")}
    r = limits._probe_claude_oauth("claude", conf)
    assert not r.ok and "credentials" in r.error


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


def test_task_file_with_bom_runs(mock_root):
    cfg = Config.load(mock_root)
    spec = {"pattern": "producer-reviewer", "task": "t", "producer": "claude-main",
            "reviewer": "codex-critic", "max_rounds": 1, "pass_score": 8.0}
    tf = mock_root / "task.json"
    tf.write_text("﻿" + json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert run_task_file(cfg, tf, auto=True) == "done"
