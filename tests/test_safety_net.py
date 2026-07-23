"""F2-1 테스트 격리 안전장치 자체를 검증한다 (tests/conftest.py의 _block_real_backend_calls).

mock을 무력화해도 claude/codex/gemini 실행파일 호출과 urlopen이 이중 안전장치로 막히는지,
그리고 verify_cmd처럼 backend가 아닌 정당한 로컬 실행은 그대로 통과하는지 확인한다.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.request

import pytest

from tests.conftest import _is_backend_cmd, _program_name
from yok3x.backends import run_backend


def test_program_name_strips_path_and_ext_case_insensitive():
    assert _program_name(["claude", "-p"]) == "claude"
    assert _program_name([r"C:\bin\CODEX.CMD", "exec"]) == "codex"
    assert _program_name("gemini --output-format json") == "gemini"     # shell=True 문자열
    assert _program_name([]) == ""


def test_is_backend_cmd_matches_only_known_backends():
    assert _is_backend_cmd(["claude", "-p", "hi"]) is True
    assert _is_backend_cmd(["codex", "exec"]) is True
    assert _is_backend_cmd("gemini --skip-trust") is True
    assert _is_backend_cmd([sys.executable, "-c", "print(1)"]) is False   # 로컬 파이썬 — verify_cmd류
    assert _is_backend_cmd("pytest -q") is False


def test_unmocked_claude_call_is_blocked_not_silently_run():
    """mock 배선 없이 claude를 그대로 호출하면(사고 시나리오) subprocess로 새지 않고 즉시 막힌다."""
    spec = {"type": "cli", "command": ["claude", "-p", "{prompt}"], "parser": "raw"}
    with pytest.raises(RuntimeError, match="안전장치"):
        run_backend("claude", spec, "hi")


def test_unmocked_codex_and_gemini_also_blocked():
    for name, cmd in (("codex", ["codex", "exec"]), ("gemini", ["gemini", "--skip-trust"])):
        spec = {"type": "cli", "command": cmd, "parser": "raw"}
        with pytest.raises(RuntimeError, match="안전장치"):
            run_backend(name, spec, "hi")


def test_non_backend_subprocess_passes_through_unblocked():
    """verify_cmd·프로세스관리처럼 claude/codex/gemini가 아닌 로컬 실행은 실제로 수행된다(회귀 방지:
    이 안전장치가 verify_cmd까지 막아버려 오히려 테스트를 깨는 걸 방지)."""
    proc = subprocess.run([sys.executable, "-c", "print('ok')"],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0 and "ok" in proc.stdout


def test_urlopen_blocked_without_mock():
    with pytest.raises(RuntimeError, match="안전장치"):
        urllib.request.urlopen("https://api.anthropic.com/api/oauth/usage", timeout=1)


def test_test_own_monkeypatch_overrides_the_guard(monkeypatch):
    """테스트가 스스로 subprocess.run을 스텁하면 그 스텁이 안전장치를 정상적으로 덮어쓴다
    (기존 test_run_cli_* 계열이 계속 동작하는 이유)."""
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    spec = {"type": "cli", "command": ["claude", "-p", "{prompt}"], "parser": "raw"}
    res = run_backend("claude", spec, "hi")
    # _run_cli가 npm .cmd 심을 전체경로로 해석해 넘긴다(BUG-10/18) — 베이스이름만 확인.
    assert _program_name(calls["cmd"]) == "claude" and res.ok


@pytest.mark.live
def test_live_marked_test_is_skipped_without_env_var():
    """live 마커만으로는 실행되지 않는다 — YOK3X_ALLOW_LIVE 없으면 setup 단계에서 스킵된다.
    (이 테스트가 본문까지 도달했다면 이중 안전장치의 2번째 축이 깨진 것)."""
    pytest.fail("YOK3X_ALLOW_LIVE 없이 live 테스트가 스킵되지 않고 실행됐다 — 안전장치 실패")
