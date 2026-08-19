import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from yok3x.config import Config
from yok3x import guiserver as gs


def _root():
    root = Path(".pytest-temp") / f"gui-automation-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    return root


def test_gui_state_exposes_automation_fields(monkeypatch):
    cfg = Config.load(_root())
    cfg.yok3x["automation_mode"] = "off"
    fake = SimpleNamespace(level="ok", ratio=0, source="test", real=False,
                           detail="", reading=None)
    monkeypatch.setattr(gs.usage, "today_totals", lambda cfg: {})
    monkeypatch.setattr(gs.usage, "check_backend", lambda cfg, name, **kw: fake)
    monkeypatch.setattr(gs.usage, "coach_messages", lambda cfg, **kw: [])
    monkeypatch.setattr(gs.limits, "claude_token_status", lambda conf: {})
    monkeypatch.setattr(gs.limits, "list_models", lambda cfg, b: [])
    monkeypatch.setattr(gs.limits, "list_models_gui", lambda cfg, b: [])
    monkeypatch.setattr(gs.backends, "effort_defaults", lambda: {})
    monkeypatch.setattr(gs, "_routing_preview", lambda cfg: [])
    monkeypatch.setattr(gs, "_profile_routes", lambda cfg: {})
    state = gs.build_state(cfg)
    assert state["automation_mode"] == "off"
    assert state["automation_options"] == cfg.yok3x["automation"]


def test_apply_config_automation_is_atomic_and_validates():
    cfg = Config.load(_root())
    cfg.ensure_dirs()
    cfg.save_yok3x()
    original = cfg.paths.yok3x_json.read_text(encoding="utf-8-sig")
    assert gs._apply_config(cfg, {"automation_mode": "assist"})["ok"]
    assert gs._apply_config(cfg, {"automation_mode": "full"})["ok"]
    assert cfg.yok3x["automation_mode"] == "full"
    result = gs._apply_config(cfg, {"automation_mode": "turbo"})
    assert not result.get("ok") and "off|assist|full" in result["error"]
    assert cfg.yok3x["automation_mode"] == "full"
    assert original != cfg.paths.yok3x_json.read_text(encoding="utf-8-sig")


def test_recent_runs_only_reads_automation_decision_from_status():
    cfg = Config.load(_root())
    run = cfg.paths.runs / "run-s8"
    run.mkdir(parents=True)
    (run / "status.json").write_text(json.dumps({
        "run_id": "run-s8", "steps": [],
        "automation_decision": {"mode": "assist", "computed": True},
    }), encoding="utf-8")
    task = cfg.paths.root / "task-s8.json"
    task.write_text('{"task":"unchanged"}', encoding="utf-8")
    before = task.read_bytes()
    rows = gs._recent_runs(cfg, 5)
    assert rows[0]["automation_decision"]["mode"] == "assist"
    assert task.read_bytes() == before
