import copy
import json

from yok3x import guiserver as gs
from yok3x.config import Config, atomic_write_text


def _cfg(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path)
    atomic_write_text(cfg.paths.backends_json,
                      json.dumps(cfg.backends, ensure_ascii=False, indent=2) + "\n")
    cfg.save_yok3x()
    monkeypatch.setattr(gs, "_refresh_gui_state_sync", lambda _cfg: None)
    return cfg


def test_backend_add_validates_name_duplicate_and_gemini(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert "이름" in gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "Bad Name", "auth_dir": "~/.bad"})["error"]
    assert "gemini" in gs._add_backend_account(
        cfg, {"account_of": "gemini", "name": "gemini-alt", "auth_dir": "~/.gemini-alt"})["error"]

    result = gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": "~/.codex-alt"})
    assert result["ok"]
    assert "이미 존재" in gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": "~/.other"})["error"]


def test_backend_add_copies_source_and_records_limit_path(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    source = copy.deepcopy(cfg.backends["claude"])
    result = gs._add_backend_account(
        cfg, {"account_of": "claude", "name": "claude-alt", "auth_dir": "~/.claude-alt"})
    assert result["ok"]
    clone = cfg.backends["claude-alt"]
    for key in ("type", "command", "parser", "model_arg", "effort_arg", "timeout_sec"):
        assert clone.get(key) == source.get(key)
    assert clone["account_of"] == "claude"
    assert clone["env"] == {"CLAUDE_CONFIG_DIR": "~/.claude-alt"}
    assert cfg.yok3x["limits"]["claude-alt"]["projects_dir"] == "~/.claude-alt/projects"
    assert json.loads(cfg.paths.backends_json.read_text(encoding="utf-8"))["claude-alt"] == clone
    assert json.loads(cfg.paths.yok3x_json.read_text(encoding="utf-8"))["limits"]["claude-alt"]["projects_dir"] == "~/.claude-alt/projects"
    assert cfg.paths.backends_json.with_name("backends.json.bak").exists()
    assert cfg.paths.yok3x_json.with_name("yok3x.json.bak").exists()


def test_backend_remove_rejects_original_and_references(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert "원본" in gs._remove_backend_account(cfg, {"name": "codex"})["error"]
    assert gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": "~/.codex-alt"})["ok"]
    cfg.yok3x["workers"]["codex-main"]["backend"] = "codex-alt"
    assert "참조 중" in gs._remove_backend_account(cfg, {"name": "codex-alt"})["error"]
    cfg.yok3x["workers"]["codex-main"]["backend"] = "codex"
    cfg.yok3x["routing"]["review"] = "codex-alt"
    assert "routing" in gs._remove_backend_account(cfg, {"name": "codex-alt"})["error"]
    cfg.yok3x["routing"]["review"] = "codex"
    assert gs._remove_backend_account(cfg, {"name": "codex-alt"})["ok"]
    assert "codex-alt" not in cfg.backends
    assert "codex-alt" not in cfg.yok3x["limits"]


def test_backend_add_save_failure_rolls_back_files_and_memory(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    before_backends = cfg.paths.backends_json.read_text(encoding="utf-8")
    before_yok3x = cfg.paths.yok3x_json.read_text(encoding="utf-8")
    before_memory = copy.deepcopy((cfg.backends, cfg.yok3x))
    real_atomic = gs.atomic_write_text
    failed = False

    def fail_yok3x_once(path, text, encoding="utf-8"):
        nonlocal failed
        if path == cfg.paths.yok3x_json and not failed:
            failed = True
            raise OSError("simulated write failure")
        return real_atomic(path, text, encoding)

    monkeypatch.setattr(gs, "atomic_write_text", fail_yok3x_once)
    result = gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": "~/.codex-alt"})
    assert "롤백" in result["error"]
    assert cfg.paths.backends_json.read_text(encoding="utf-8") == before_backends
    assert cfg.paths.yok3x_json.read_text(encoding="utf-8") == before_yok3x
    assert (cfg.backends, cfg.yok3x) == before_memory


def test_dynamic_limits_backend_names_includes_account_clone(tmp_path):
    cfg = Config.load(tmp_path)
    cfg.backends["codex-alt"] = {"account_of": "codex"}
    from yok3x import usage
    assert "codex-alt" in usage.limits_backend_names(cfg)


def test_limits_backend_names_excludes_quotaless_stubs(tmp_path):
    """회귀: 계정 복제를 보이게 하면서 mock·local(구독 쿼터 없음)까지 같이 뜨던 문제.

    옛 고정 튜플이 일부러 빼두던 스텁이 동적 전환 때 되살아나 '구독 한도 대비' 표에
    0%로 상주했다(사용자 지적). 복제는 남고 스텁만 빠져야 한다.
    """
    cfg = Config.load(tmp_path)
    cfg.backends["codex-alt"] = {"account_of": "codex"}
    from yok3x import usage
    names = usage.limits_backend_names(cfg)

    assert "codex-alt" in names                      # 계정 복제는 보여야 한다
    assert {"claude", "codex", "gemini"} <= set(names)
    assert "mock" not in names and "local" not in names


# ---------------------------------------------------------------- API 키 방식 계정 추가

def test_backend_add_api_key_sets_env_and_ledger_limit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    result = gs._add_backend_account(
        cfg, {"account_of": "claude", "name": "claude-api", "auth_mode": "api_key",
              "api_key": "sk-ant-test123"})
    assert result["ok"]
    clone = cfg.backends["claude-api"]
    assert clone["account_of"] == "claude"
    assert clone["env"] == {"ANTHROPIC_API_KEY": "sk-ant-test123"}
    assert cfg.yok3x["limits"]["claude-api"] == {"type": "ledger", "api_key_env": "ANTHROPIC_API_KEY"}
    # OAuth 전용 필드(claude_oauth가 보는 경로)를 물려받지 않아야 한다 — 물려받으면 원본
    # 계정의 실제 OAuth 사용률을 API 키 계정 것인 양 잘못 보고할 위험이 있다.
    assert "projects_dir" not in cfg.yok3x["limits"]["claude-api"]
    assert "credentials_path" not in cfg.yok3x["limits"]["claude-api"]

    result2 = gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-api", "auth_mode": "api_key",
              "api_key": "sk-oai-test456"})
    assert result2["ok"]
    assert cfg.backends["codex-api"]["env"] == {"OPENAI_API_KEY": "sk-oai-test456"}
    assert cfg.yok3x["limits"]["codex-api"] == {"type": "ledger", "api_key_env": "OPENAI_API_KEY"}


def test_backend_add_api_key_requires_key_and_rejects_gemini(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert "API 키" in gs._add_backend_account(
        cfg, {"account_of": "claude", "name": "claude-api", "auth_mode": "api_key"})["error"]
    assert "gemini" in gs._add_backend_account(
        cfg, {"account_of": "gemini", "name": "gemini-api", "auth_mode": "api_key",
              "api_key": "AIza-test"})["error"]


def test_backend_account_row_exposes_auth_mode_without_leaking_key(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-api", "auth_mode": "api_key",
              "api_key": "sk-oai-secret"})["ok"]
    rows = {r["name"]: r for r in gs._backend_account_rows(cfg)}
    row = rows["codex-api"]
    assert row["auth_mode"] == "api_key"
    assert row["auth_dir"] == ""            # 디렉터리 방식이 아니므로 경로를 보여주지 않는다
    assert row["connected"] is True         # 키가 저장돼 있으면 '연결됨'
    assert row["login_command"] == ""       # API 키 방식은 터미널 로그인이 필요 없다
    dumped = json.dumps(row, ensure_ascii=False)
    assert "sk-oai-secret" not in dumped    # 원본 키 값이 응답에 절대 섞이면 안 된다

    dir_row = rows["codex"]
    assert dir_row["auth_mode"] == "dir"


def test_limits_backend_names_honours_configured_offline_backend(tmp_path):
    """제외 대상은 하드코딩이 아니라 설정된 offline_backend를 따른다(RULE §5.5)."""
    cfg = Config.load(tmp_path)
    cfg.backends["sim"] = {"type": "openai_http"}
    cfg.yok3x.setdefault("guard", {}).setdefault("degrade", {})["offline_backend"] = "sim"
    from yok3x import usage
    names = usage.limits_backend_names(cfg)

    assert "sim" not in names                        # 설정된 오프라인 backend가 빠지고
    assert "local" in names                          # 기본값 'local'은 더 이상 특별하지 않다


def test_claude_clone_gets_credentials_path_not_just_projects_dir(tmp_path):
    """회귀: claude 복제에 credentials_path가 없어 원본 계정 쿼터를 자기 것처럼 real=True로 보고.

    type=claude_oauth의 라이브 실측은 `credentials_path`(limits.py `_claude_token`)를 보고,
    `projects_dir`는 트랜스크립트 추정 폴백만 본다. projects_dir만 쓰면 실측이 원본 토큰을
    읽어 claude와 claude-alt가 완전히 같은 수치를 낸다(실측 확인).
    """
    cfg = Config.load(tmp_path)
    cfg.yok3x.setdefault("limits", {})["claude"] = {"type": "claude_oauth", "plan": "max5x"}

    res = gs._add_backend_account(
        cfg, {"account_of": "claude", "name": "claude-alt", "auth_dir": "~/.claude-alt"})

    assert res.get("ok"), res
    conf = json.loads(cfg.paths.yok3x_json.read_text(encoding="utf-8"))["limits"]["claude-alt"]
    assert conf["credentials_path"] == "~/.claude-alt/.credentials.json"
    assert conf["projects_dir"] == "~/.claude-alt/projects"
    assert conf["credentials_path"] != "~/.claude/.credentials.json"


def test_codex_clone_gets_sessions_dir_only(tmp_path):
    """codex는 app-server가 CODEX_HOME(=sessions_dir의 부모)을 쓰므로 sessions_dir면 충분하다."""
    cfg = Config.load(tmp_path)
    cfg.yok3x.setdefault("limits", {})["codex"] = {"type": "codex_appserver"}

    res = gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": "~/.codex-alt"})

    assert res.get("ok"), res
    conf = json.loads(cfg.paths.yok3x_json.read_text(encoding="utf-8"))["limits"]["codex-alt"]
    assert conf["sessions_dir"] == "~/.codex-alt/sessions"
    assert "credentials_path" not in conf


def test_unauthenticated_clone_is_not_a_failover_candidate(tmp_path):
    """미로그인 복제는 폴오버 후보에서 빠진다(사용자 지시 2026-09-07).

    실측이 없으면 원장 폴백이 level=ok·ratio=0으로 나와 **가장 여유로운 후보처럼 보이고**,
    폴오버가 그걸 골라 CLI 인증 오류로 런이 죽었다(claude warn 0.98 상태에서 미로그인
    claude-alt가 선택됨).
    """
    from yok3x import usage
    cfg = Config.load(tmp_path)
    alt_home = tmp_path / "claude-alt-home"
    alt_home.mkdir()
    cfg.backends["claude-alt"] = {
        "type": "cli", "command": ["claude"], "account_of": "claude",
        "env": {"CLAUDE_CONFIG_DIR": str(alt_home)},
    }

    assert usage.account_logged_in(cfg, "claude-alt") is False
    assert usage.backend_available(cfg, "claude-alt") is False

    (alt_home / ".credentials.json").write_text("{}", encoding="utf-8")
    assert usage.account_logged_in(cfg, "claude-alt") is True


def test_original_backend_is_never_blocked_by_credential_check(tmp_path):
    """원본은 판정 대상이 아니다 — 키체인 등 다른 경로를 쓸 수 있어 막으면 훨씬 위험하다."""
    from yok3x import usage
    cfg = Config.load(tmp_path)
    assert usage.account_logged_in(cfg, "claude") is None
    assert usage.account_logged_in(cfg, "codex") is None


def test_clone_without_known_env_convention_is_not_blocked(tmp_path):
    """규약을 모르는 복제(env 없음/미지원 계정군)는 막지 않는다 — 판정 불가는 차단 사유가 아니다."""
    from yok3x import usage
    cfg = Config.load(tmp_path)
    cfg.backends["weird-alt"] = {"type": "cli", "command": ["claude"], "account_of": "claude"}
    assert usage.account_logged_in(cfg, "weird-alt") is None


def test_backend_card_order_saves_and_validates(tmp_path):
    """카드 순서 저장 — 알 수 없는 계정군·중복은 거부하고, 저장값은 에코해 GUI가 확인할 수 있게."""
    cfg = Config.load(tmp_path)
    cfg.backends["codex-alt"] = {"account_of": "codex"}

    assert "배열" in gs._apply_config(cfg, {"backend_card_order": "codex"})["error"]
    assert "알 수 없는" in gs._apply_config(cfg, {"backend_card_order": ["nope"]})["error"]
    assert "중복" in gs._apply_config(cfg, {"backend_card_order": ["codex", "codex"]})["error"]

    res = gs._apply_config(cfg, {"backend_card_order": ["codex", "claude"]})
    assert res["ok"] and res["backend_card_order"] == ["codex", "claude"]
    saved = json.loads(cfg.paths.yok3x_json.read_text(encoding="utf-8"))
    assert saved["gui"]["backend_card_order"] == ["codex", "claude"]


def test_backend_card_order_accepts_partial_list(tmp_path):
    """전부 나열하지 않아도 된다 — 새 backend가 생겨도 순서 설정 때문에 화면에서 사라지면 안 되므로
    GUI가 누락분을 뒤에 붙인다(applyBackendCardOrder). 서버는 '전부 나열'을 요구하지 않는다."""
    cfg = Config.load(tmp_path)
    res = gs._apply_config(cfg, {"backend_card_order": ["gemini"]})
    assert res["ok"] and res["backend_card_order"] == ["gemini"]


def test_state_exposes_gui_settings(tmp_path, monkeypatch):
    """저장한 순서를 /api/state가 돌려줘야 새로고침 후에도 유지된다."""
    cfg = Config.load(tmp_path)
    cfg.yok3x.setdefault("gui", {})["backend_card_order"] = ["codex", "claude"]
    monkeypatch.setattr(gs, "_routing_preview", lambda c: [])
    monkeypatch.setattr(gs.usage, "coach_messages", lambda c, probe_fn=None: [])
    monkeypatch.setattr(gs.usage, "check_backend",
                        lambda c, b, probe_fn=None: gs.usage.GuardVerdict(b, 0.0, "5h", "ok", "d"))

    state = gs.build_state(cfg)

    assert state["gui"]["backend_card_order"] == ["codex", "claude"]


def test_account_rows_exclude_quotaless_stubs(tmp_path):
    """계정 카드도 사용량 패널과 같은 목록을 쓴다 — mock·local은 로그인할 계정이 없어
    복제도 못 하는 빈 카드가 된다(사용자 지적: "모크랑 이런 것도 보이고")."""
    cfg = Config.load(tmp_path)
    cfg.backends["codex-alt"] = {"account_of": "codex"}

    names = [r["name"] for r in gs._backend_account_rows(cfg)]

    assert "codex-alt" in names and {"claude", "codex", "gemini"} <= set(names)
    assert "mock" not in names and "local" not in names


def _swap_setup(tmp_path, monkeypatch):
    """스왑은 이름-경로 매핑이 아니라 **실제 디렉터리 내용**을 맞바꾼다 — yok3x.json 안에서만
    바꾸면 이 저장소 밖(다른 세션·터미널)에는 반영이 안 된다는 사용자 지적 때문(2026-09-10).
    그래서 테스트도 실제 tmp_path 디렉터리 두 개를 만들어 진짜로 rename되는지 확인한다."""
    cfg = _cfg(tmp_path, monkeypatch)
    original_dir = tmp_path / "home" / ".codex"
    clone_dir = tmp_path / "home" / ".codex-alt"
    (original_dir).mkdir(parents=True)
    (clone_dir).mkdir(parents=True)
    (original_dir / "marker.txt").write_text("original", encoding="utf-8")
    (clone_dir / "marker.txt").write_text("clone", encoding="utf-8")

    def fake_auth_dir(name, spec):
        return str(clone_dir) if name == "codex-alt" else str(original_dir)

    monkeypatch.setattr(gs, "_backend_auth_dir", fake_auth_dir)
    assert gs._add_backend_account(
        cfg, {"account_of": "codex", "name": "codex-alt", "auth_dir": str(clone_dir)})["ok"]
    return cfg, original_dir, clone_dir


def test_swap_rejects_non_clone_and_unsupported_family(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert "복제" in gs._swap_backend_account_dirs(cfg, {"name": "codex"})["error"]
    assert gs._add_backend_account(
        cfg, {"account_of": "claude", "name": "claude-alt", "auth_dir": str(tmp_path / "calt")})["ok"]
    assert "claude" in gs._swap_backend_account_dirs(cfg, {"name": "claude-alt"})["error"]


def test_swap_exchanges_real_directory_contents(tmp_path, monkeypatch):
    cfg, original_dir, clone_dir = _swap_setup(tmp_path, monkeypatch)

    result = gs._swap_backend_account_dirs(cfg, {"name": "codex-alt"})

    assert result["ok"], result
    assert (original_dir / "marker.txt").read_text(encoding="utf-8") == "clone"
    assert (clone_dir / "marker.txt").read_text(encoding="utf-8") == "original"
    # 임시 폴더가 안 남아야 한다(3단계 rename이 끝까지 마무리됨).
    assert not list(original_dir.parent.glob("*.yok3x-swap-tmp"))


def test_swap_missing_dir_reports_error_without_partial_rename(tmp_path, monkeypatch):
    cfg, original_dir, clone_dir = _swap_setup(tmp_path, monkeypatch)
    import shutil as _shutil
    _shutil.rmtree(clone_dir)

    result = gs._swap_backend_account_dirs(cfg, {"name": "codex-alt"})

    assert "error" in result
    assert (original_dir / "marker.txt").read_text(encoding="utf-8") == "original"  # 안 건드려짐


def test_kill_codex_processes_runs_taskkill_for_each_known_process(monkeypatch):
    """스왑이 파일 잠금으로 막혔을 때만 쓰는 별도 동작(2026-09-10) — 실제 taskkill은 절대
    호출하지 않고, 어떤 이름으로 몇 번 불렀는지만 검증한다."""
    calls = []

    class FakeCompleted:
        def __init__(self, returncode=0, stdout="SUCCESS: 종료됨."):
            self.returncode, self.stdout, self.stderr = returncode, stdout, ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeCompleted()

    monkeypatch.setattr(gs.subprocess, "run", fake_run)

    result = gs._kill_codex_processes()

    assert result["ok"]
    killed_names = {c[c.index("/IM") + 1] for c in calls}
    assert killed_names == set(gs._CODEX_PROCESS_NAMES)
    assert all(r["ok"] for r in result["results"])
