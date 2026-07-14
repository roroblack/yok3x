"""yok3x 회귀 잠금 테스트.

손으로 매번 확인하던 것을 자동화한다(RULE §5: 실행해서 확인). mock 백엔드 위에서
외부 도구 없이 결정적으로 돈다. 실행: 프로젝트 루트에서 `pytest` 또는 `python -m pytest`.
verify_cmd 게이트에 `pytest -q`를 걸면 프로젝트가 자기 자신을 dogfooding하게 된다.
"""
from __future__ import annotations

import json

import subprocess

import pytest

from yok3x import __version__, backends, limits, matview, orchestrator, usage
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
