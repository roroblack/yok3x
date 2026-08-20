import json
import subprocess
import sys

import pytest

from yok3x import backends
from yok3x.config import Config


def test_cli_prompt_metacharacters_stay_in_one_argv_value(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends.shutil, "which", lambda value: None)
    prompt = "; | & ` $()\nnot-a-command"
    result = backends.run_backend(
        "local-cli", {"type": "cli", "command": ["tool", "{prompt}"], "parser": "raw"}, prompt
    )

    assert result.ok
    assert seen["cmd"] == ["tool"]
    assert seen["kwargs"]["input"] == prompt
    assert "shell" not in seen["kwargs"]


@pytest.mark.parametrize("prompt", [
    pytest.param("a\x00b", id="nul"),
    pytest.param("a\ud800b", id="surrogate"),
    pytest.param("x" * 200_000, id="large"),
])
def test_cli_edge_prompt_is_stdin_safe(monkeypatch, prompt):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends.shutil, "which", lambda value: None)
    result = backends.run_backend(
        "local-cli", {"type": "cli", "command": ["tool", "-p", "{prompt}"], "parser": "raw"}, prompt
    )

    assert result.ok
    assert "{prompt}" not in seen["cmd"]
    assert seen["kwargs"]["input"] == prompt


def test_config_non_object_json_falls_back_to_defaults(tmp_path, caplog):
    (tmp_path / "yok3x.json").write_text("[]", encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.yok3x["flavor"] == "claude-orchestrator"
    assert "root" in caplog.text


def test_config_deep_json_does_not_leak_recursion_error(tmp_path):
    value = current = {}
    for _ in range(900):
        current["nested"] = {}
        current = current["nested"]
    (tmp_path / "yok3x.json").write_text(json.dumps(value), encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.yok3x["flavor"] == "claude-orchestrator"


def test_cli_unknown_command_is_argparse_error():
    from yok3x import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["does-not-exist"])
    assert exc.value.code == 2
