"""yok3x gui — 로컬 http 서버로 GUI + 실데이터 JSON을 제공(의존성 0).

GUI(gui/index.html)를 브라우저에 띄우고,
  GET  /api/state   실제 limits/coach/runs/큐 상태
  POST /api/run     인라인 태스크 spec 또는 등록된 task 파일 실행(큐)
  POST /api/config  워커 backend·routing·flavor 편집 → yok3x.json 저장(검증+백업)
프로토타입이지만 목업이 아니라 진짜 yok3x 데이터로 동작한다.
"""
from __future__ import annotations

import http.server
import copy
import json
import logging
import re
import signal
import shutil
import socketserver
import subprocess
import threading
import webbrowser
from datetime import datetime
import time
from pathlib import Path

from . import backends, limits, sync_layer, usage
from ._version import __version__
from .config import Config, atomic_write_text
from .automation import validate_automation_config, validate_automation_mode, validate_task_automation_mode

EFFORTS_OK = ("minimal", "low", "medium", "high", "xhigh", "max")
APPLY_MODES = ("review", "auto_commit")
BACKEND_ACCOUNT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# 단일 출처는 usage.py — 표시(여기)와 폴오버 판정(usage.backend_available)이 어긋나면
# 화면엔 "⚠ 미로그인"인데 폴오버는 그 계정을 고르는 상태가 난다.
BACKEND_ACCOUNT_ENV = usage.ACCOUNT_ENV_KEYS
# API 키 방식 계정 추가(2026-09-14, 사용자 요청): claude/codex CLI 모두 구독 로그인(OAuth) 대신
# API 키로도 돌아간다 — gemini가 이미 그렇게 쓰던 것과 같은 패턴. 디렉터리 격리(BACKEND_ACCOUNT_ENV)
# 대신 이 env 변수 하나만 주입하면 해당 계정은 API 과금으로 동작하고, 실측 프로브가 없으니
# limits.type="ledger"(원장) 폴백으로 표시된다 — 이미 있는 gemini 카드와 같은 배지/모양.
API_KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "codex": "OPENAI_API_KEY"}

# 실행 상태 + 큐. 단일 실행 락으로 동시 실행 방지, 나머지는 큐 대기.
# last: 직전 실행 결과/오류를 보존해 GUI에 노출(조용한 실패 금지).
_RUN_STATE = {"active": False, "task": None, "since": None, "last": None}
_QUEUE: list[tuple[str, int]] = []   # (task_file, iterations)
_LOCK = threading.Lock()
_CLAUDE_LOGIN_STARTED_AT = None
_GUI_LOGGER = logging.getLogger("yok3x.gui")
_GUI_STATE: dict | None = None
_GUI_STATE_BUILT_AT = 0.0
_GUI_STATE_REFRESHING = False
_GUI_STATE_GUARD = threading.Condition()
_GUI_STATE_REFRESH_SEC = 5.0


def _configure_gui_logging(cfg: Config) -> None:
    path = cfg.paths.yok3x_dir / "guiserver.log"
    cfg.paths.yok3x_dir.mkdir(parents=True, exist_ok=True)
    for handler in _GUI_LOGGER.handlers:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == path.resolve():
            return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z"))
    _GUI_LOGGER.addHandler(handler)
    _GUI_LOGGER.setLevel(logging.INFO)
    _GUI_LOGGER.propagate = False
    _GUI_LOGGER.info("GUI server logging enabled path=%s", path)


def _flush_gui_logging() -> None:
    """Best-effort final flush for shutdown diagnostics."""
    for handler in _GUI_LOGGER.handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _claude_login_start() -> dict:
    """Start Claude's browser-based OAuth flow and leave it running."""
    global _CLAUDE_LOGIN_STARTED_AT
    resolved = shutil.which("claude")
    if not resolved:
        return {"ok": False, "error": "Claude CLI를 찾을 수 없습니다."}
    try:
        proc = subprocess.Popen(
            [resolved, "auth", "login", "--claudeai"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Claude 로그인 시작 실패: {exc}"}

    # The CLI opens the browser and receives the local callback itself. Reap it
    # after it exits, but never terminate it while the user is authenticating.
    threading.Thread(target=proc.wait, daemon=True).start()
    _CLAUDE_LOGIN_STARTED_AT = time.time()
    return {"ok": True}


def _claude_login_status(cfg: Config) -> dict:
    conf = (cfg.yok3x.get("limits") or {}).get("claude") or {}
    status = limits.claude_token_status(conf)
    return {"exists": bool(status.get("exists")), "expired": status.get("expired")}


def _routing_preview(cfg: Config) -> list:
    """활성 프로파일의 상황별 라우팅(가용성·한도 반영) — GUI '왜 이 모델' 표시용.
    프로파일 off면 빈 리스트(성능: 폴링마다 probe 안 함)."""
    if not (cfg.yok3x.get("active_profile") or "").strip():
        return []
    from .orchestrator import resolve_model
    # resolve_model() may ask about the same backend for several route kinds.
    # Keep that check local to one state build: backend_available() can itself
    # run check_backend(), so repeating it here needlessly extends /api/state.
    availability: dict[str, bool] = {}

    def avail(b: str) -> bool:
        if b not in availability:
            availability[b] = usage.backend_available(cfg, b, probe_fn=limits.probe_gui)
        return availability[b]
    out = []
    for kind, label in (("build", "구현"), ("review", "검수"), ("design_review", "설계검토")):
        b, m, why = resolve_model(cfg, kind, available=avail)
        out.append({"kind": kind, "label": label, "backend": b, "model": m, "why": why})
    return out


def _profile_routes(cfg: Config, available=None) -> dict:
    """각 프로파일이 상황별로 어떤 모델을 고르는지(설명용). 가용성 미반영 '이론상' 픽."""
    from .orchestrator import resolve_model
    out = {}
    for pname in cfg.yok3x.get("profiles", {}):
        rows = []
        for kind, label in (("build", "구현"), ("review", "검수"), ("design_review", "설계검토")):
            b, m, _ = resolve_model(cfg, kind, profile=pname, available=available)
            rows.append({"kind": kind, "label": label, "backend": b, "model": m})
        out[pname] = rows
    return out


def pick_directory() -> str | None:
    """서버(로컬) 머신에서 네이티브 폴더 선택 대화상자를 띄워 경로 반환. 취소 시 None.
    tkinter를 별도 서브프로세스로 격리 실행(스레드/Tk 안전, 블로킹 방지)."""
    import subprocess as _sp
    import sys as _sys
    code = ("import tkinter,tkinter.filedialog as fd;"
            "r=tkinter.Tk();r.withdraw();r.attributes('-topmost',True);"
            "p=fd.askdirectory();print(p or '')")
    try:
        r = _sp.run([_sys.executable, "-c", code], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120)
        p = (r.stdout or "").strip().splitlines()
        return (p[-1].strip() or None) if p else None
    except Exception:
        return None


def _backend_auth_mode(spec: dict) -> str:
    """'dir'(계정 로그인 디렉터리 격리) 또는 'api_key'(API 키 과금) — env에 실린 변수 이름으로 판정.
    둘 다 없는 원본 backend(claude/codex 자체)는 'dir'로 취급(기존 화면과 동일하게 표시)."""
    env = spec.get("env") or {}
    if any(k in env for k in API_KEY_ENV.values()):
        return "api_key"
    return "dir"


def _backend_auth_dir(name: str, spec: dict) -> str:
    """Return the configured credential directory without reading credentials."""
    if _backend_auth_mode(spec) == "api_key":
        return ""
    family = str(spec.get("account_of") or name)
    env_key = BACKEND_ACCOUNT_ENV.get(family)
    if env_key:
        configured = (spec.get("env") or {}).get(env_key)
        if configured:
            return str(configured)
        return f"~/.{family}"
    return ""


def _backend_auth_connected(name: str, spec: dict) -> bool | None:
    """Check only whether the CLI's local credential file exists (dir 방식) 또는 키가
    저장돼 있는지(api_key 방식) — 어느 쪽도 실제 키 값을 반환하지 않는다."""
    family = str(spec.get("account_of") or name)
    if _backend_auth_mode(spec) == "api_key":
        env_key = API_KEY_ENV.get(family)
        return bool(env_key and (spec.get("env") or {}).get(env_key))
    auth_dir = _backend_auth_dir(name, spec)
    if not auth_dir:
        return None
    filename = usage.ACCOUNT_CRED_FILE.get(family, "auth.json")
    return (Path(auth_dir).expanduser() / filename).is_file()


def _backend_login_command(name: str, spec: dict) -> str:
    family = str(spec.get("account_of") or name)
    if _backend_auth_mode(spec) == "api_key":
        return ""   # API 키 방식은 터미널 로그인이 필요 없다.
    auth_dir = _backend_auth_dir(name, spec)
    env_key = BACKEND_ACCOUNT_ENV.get(family)
    if not env_key or not auth_dir:
        return ""
    login = "claude auth login --claudeai" if family == "claude" else "codex login"
    return f"{env_key}={auth_dir} {login}"


def _backend_account_rows(cfg: Config) -> list[dict]:
    # 계정 카드도 사용량 패널과 **같은 목록**을 써야 한다. cfg.backends 전체를 돌면 `mock`(드라이런
    # 스텁)·`local`(자체 호스팅 서버)까지 카드가 생기는데, 둘 다 로그인할 계정이 없어 복제도 못 하는
    # 빈 카드다(사용자 지적: "모크랑 이런 것도 보이고").
    visible = set(usage.limits_backend_names(cfg))
    rows = []
    for name, raw_spec in cfg.backends.items():
        if name not in visible:
            continue
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        family = str(spec.get("account_of") or name)
        rows.append({
            "name": name,
            "account_of": str(spec.get("account_of") or ""),
            "family": family,
            "auth_mode": _backend_auth_mode(spec),
            "auth_dir": _backend_auth_dir(name, spec),
            "connected": _backend_auth_connected(name, spec),
            "login_command": _backend_login_command(name, spec),
            "can_clone": not spec.get("account_of") and family in BACKEND_ACCOUNT_ENV,
            "clone_disabled_reason": (
                "Gemini CLI는 인증 디렉터리를 안전하게 격리할 수 없습니다."
                if family == "gemini" else
                ("Claude/Codex CLI 계정만 복제할 수 있습니다." if family not in BACKEND_ACCOUNT_ENV else "")
            ),
        })
    return rows


def _backend_reference_error(cfg: Config, name: str) -> str:
    workers = [w for w, spec in (cfg.yok3x.get("workers") or {}).items()
               if isinstance(spec, dict) and spec.get("backend") == name]
    routes = [route for route, backend in (cfg.yok3x.get("routing") or {}).items()
              if backend == name]
    refs = []
    if workers:
        refs.append("워커 " + ", ".join(workers))
    if routes:
        refs.append("routing " + ", ".join(routes))
    return "; ".join(refs)


def _write_account_files(cfg: Config, new_backends: dict, new_yok3x: dict) -> dict:
    """Persist both account files as one logical change, restoring either on failure."""
    targets = (cfg.paths.backends_json, cfg.paths.yok3x_json)
    originals: dict[Path, str | None] = {}
    try:
        for path in targets:
            originals[path] = path.read_text(encoding="utf-8-sig") if path.exists() else None
        for path, original in originals.items():
            if original is not None:
                atomic_write_text(path.parent / (path.name + ".bak"), original)
    except OSError as exc:
        return {"error": f"백업 실패 — 저장하지 않음: {exc}"}

    written: list[Path] = []
    try:
        atomic_write_text(cfg.paths.backends_json,
                          json.dumps(new_backends, ensure_ascii=False, indent=2) + "\n")
        written.append(cfg.paths.backends_json)
        atomic_write_text(cfg.paths.yok3x_json,
                          json.dumps(new_yok3x, ensure_ascii=False, indent=2) + "\n")
        written.append(cfg.paths.yok3x_json)
    except OSError as exc:
        rollback_errors = []
        for path in reversed(written):
            original = originals[path]
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, original)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.name}: {rollback_exc}")
        detail = f"저장 실패 — 롤백됨: {exc}"
        if rollback_errors:
            detail += " (롤백 오류: " + "; ".join(rollback_errors) + ")"
        return {"error": detail}

    cfg.backends = new_backends
    cfg.yok3x = new_yok3x
    limits.clear_cache()
    _refresh_gui_state_sync(cfg)
    return {"ok": True}


def _add_backend_account(cfg: Config, body: dict) -> dict:
    account_of = str(body.get("account_of") or "").strip()
    name = str(body.get("name") or "").strip()
    auth_mode = str(body.get("auth_mode") or "dir").strip()
    auth_dir = str(body.get("auth_dir") or "").strip()
    api_key = str(body.get("api_key") or "").strip()
    replace_name = str(body.get("replace_name") or "").strip()
    if not BACKEND_ACCOUNT_NAME_RE.fullmatch(name):
        return {"error": "이름은 소문자·숫자로 시작하는 1~32자의 소문자/숫자/_/-만 허용합니다."}
    if auth_mode not in ("dir", "api_key"):
        return {"error": f"알 수 없는 인증 방식: {auth_mode}"}
    if auth_mode == "dir" and not auth_dir:
        return {"error": "인증 디렉터리를 입력하세요."}
    if auth_mode == "api_key" and not api_key:
        return {"error": "API 키를 입력하세요."}
    source = cfg.backends.get(account_of)
    if not isinstance(source, dict):
        return {"error": f"없는 원본 backend: {account_of}"}
    if source.get("account_of"):
        return {"error": "계정 복제 backend를 다시 복제할 수 없습니다."}
    if account_of == "gemini":
        return {"error": "gemini는 인증 디렉터리를 안전하게 격리할 수 없어 계정 추가를 지원하지 않습니다."}
    if auth_mode == "dir":
        env_key = BACKEND_ACCOUNT_ENV.get(account_of)
        if not env_key:
            return {"error": f"계정 복제를 지원하지 않는 backend: {account_of}"}
    else:
        env_key = API_KEY_ENV.get(account_of)
        if not env_key:
            return {"error": f"API 키 방식을 지원하지 않는 backend: {account_of}"}
    if name in cfg.backends and name != replace_name:
        return {"error": f"이미 존재하는 backend 이름: {name}"}
    if replace_name:
        replaced = cfg.backends.get(replace_name)
        if not isinstance(replaced, dict) or not replaced.get("account_of"):
            return {"error": "편집 대상은 등록된 계정 복제 backend여야 합니다."}
        if name != replace_name:
            refs = _backend_reference_error(cfg, replace_name)
            if refs:
                return {"error": f"참조 중인 backend 이름은 바꿀 수 없습니다: {refs}"}

    new_backends = copy.deepcopy(cfg.backends)
    new_yok3x = copy.deepcopy(cfg.yok3x)
    if replace_name and replace_name != name:
        new_backends.pop(replace_name, None)
        (new_yok3x.get("limits") or {}).pop(replace_name, None)
    clone = copy.deepcopy(source)
    clone["account_of"] = account_of
    limits_map = new_yok3x.setdefault("limits", {})

    if auth_mode == "api_key":
        # gemini와 같은 패턴: 디렉터리 격리 대신 API 키 하나만 env로 주입한다. 실측 프로브가
        # 없으니 limits.type을 명시적으로 "ledger"(원장)로 둔다 — claude_oauth/codex_appserver를
        # 그대로 물려받으면(아래 dir 분기처럼) 원본의 자격증명 파일을 읽어 **API 키 계정인데
        # OAuth 구독 사용률을 자기 것으로 잘못 보고**할 수 있다.
        clone["env"] = {env_key: api_key}
        limits_map[name] = {"type": "ledger", "api_key_env": env_key}
    else:
        clone["env"] = {env_key: auth_dir}
        limit_conf = copy.deepcopy(limits_map.get(account_of) or {})
        base = auth_dir.rstrip("/\\")
        if account_of == "claude":
            # 트랜스크립트 **추정** 폴백이 보는 경로.
            limit_conf["projects_dir"] = base + "/projects"
            # 라이브 실측(type=claude_oauth)이 보는 경로. 이걸 빼면 복제본이 원본의
            # `~/.claude/.credentials.json`을 읽어 **원본 계정의 사용률을 자기 것인 양 real=True로
            # 보고한다**(실측: claude와 claude-alt가 `5h 35% · 7d 14%`로 완전히 동일). 폴오버가
            # 여유 있다고 오판할 수 있어 조용한 오보다 — 파일이 없으면 폴백해 '미측정'으로 정직하게
            # 표시되는 쪽이 맞다.
            limit_conf["credentials_path"] = base + "/.credentials.json"
        else:
            limit_conf["sessions_dir"] = base + "/sessions"
        limits_map[name] = limit_conf

    new_backends[name] = clone
    result = _write_account_files(cfg, new_backends, new_yok3x)
    if result.get("ok"):
        result.update({"name": name, "login_command": _backend_login_command(name, clone)})
    return result


def _remove_backend_account(cfg: Config, body: dict) -> dict:
    name = str(body.get("name") or "").strip()
    spec = cfg.backends.get(name)
    if not isinstance(spec, dict):
        return {"error": f"없는 backend: {name}"}
    if not spec.get("account_of"):
        return {"error": f"원본 backend는 삭제할 수 없습니다: {name}"}
    refs = _backend_reference_error(cfg, name)
    if refs:
        return {"error": f"참조 중인 backend는 삭제할 수 없습니다: {refs}"}
    new_backends = copy.deepcopy(cfg.backends)
    new_yok3x = copy.deepcopy(cfg.yok3x)
    new_backends.pop(name, None)
    (new_yok3x.get("limits") or {}).pop(name, None)
    return _write_account_files(cfg, new_backends, new_yok3x)


# 계정 스왑(2026-09-10, 사용자 요청): "이 복제를 메인으로" — yok3x 설정만 바꾸는 게 아니라
# **실제 인증 디렉터리 내용을 통째로 맞바꾼다**. yok3x.json/backends.json 안에서만 이름-경로
# 매핑을 바꾸면 이 저장소를 통해 실행하는 워커만 바뀌고, 사용자가 터미널에서 직접 치는 `codex`나
# 다른 세션·데스크톱 앱은 여전히 옛 계정을 본다(사용자 지적: "다른 세션에서도 반영이 돼야
# 의미가 있다") — 그래서 이름은 그대로 두고 그 이름이 가리키는 **디렉터리의 내용**을 바꾼다.
# gemini는 지원 안 함(원래도 계정 복제 자체가 안 됨). claude도 지금은 막는다 — `~/.claude`는
# 이 세션 자신이 지금 쓰고 있는 폴더라, 실행 중에 통째로 바꿔치기하면 이 세션 자체가 깨질 위험이
# 있다(codex는 yok3x가 그때그때 서브프로세스로만 띄우므로 상대적으로 안전).
_SWAPPABLE_FAMILIES = {"codex"}


# 스왑이 파일 잠금(codex.exe 등 실행 중)으로 막혔을 때 쓰는 **별도의, 명시적** 종료 동작
# (2026-09-10, 사용자 요청 "저거 누르면 그냥 프로세스 다 종료하게 하면 안됨?"). 스왑 버튼 자체에
# 자동으로 끼워넣지 않는다 — 사용자가 모르는 사이에 다른 codex 작업(예: 다른 세션이 지금
# 쓰고 있는 것)을 끊어버릴 수 있어서, 항상 그 자체로 별도 확인을 거치는 동작이어야 한다.
# 순서 중요: codex 본인한테 직접 물어본 결과(2026-09-14) — codex-code-mode-host.exe가 데스크톱
# 앱의 감시자(watchdog)라 codex.exe를 죽여도 이게 살아있으면 곧 다시 띄운다. 감시자를 먼저
# 죽여야 codex.exe가 재실행 안 된다(반대 순서였던 게 "3번 시도해도 계속 다시 뜬다"의 원인 중
# 하나였을 것 — 매번 codex.exe를 먼저 죽이고 나서 감시자를 죽이니 그 사이 틈에 재실행됐을 수 있음).
_CODEX_PROCESS_NAMES = ("codex-code-mode-host.exe", "codex.exe")


def _kill_codex_processes() -> dict:
    if shutil.which("taskkill") is None:
        return {"error": "taskkill을 찾을 수 없습니다(Windows 전용 기능)."}
    results = []
    for proc_name in _CODEX_PROCESS_NAMES:
        try:
            r = subprocess.run(["taskkill", "/IM", proc_name, "/F"],
                               capture_output=True, text=True, timeout=10)
            # taskkill은 대상이 아예 없어도 실패 종료코드를 내는데, 그건 "이미 안 떠 있음"이라
            # 스왑 관점에서는 성공과 같다 — 메시지로 구분해 보여주되 전체를 실패로 취급 안 한다.
            detail = (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [""]
            results.append({"name": proc_name, "ok": True, "detail": detail[0][:200]})
        except Exception as e:
            results.append({"name": proc_name, "ok": False, "detail": f"{type(e).__name__}: {e}"})
    return {"ok": True, "results": results}


def _swap_backend_account_dirs(cfg: Config, body: dict) -> dict:
    name = str(body.get("name") or "").strip()
    clone_spec = cfg.backends.get(name)
    if not isinstance(clone_spec, dict) or not clone_spec.get("account_of"):
        return {"error": "계정 복제(alt) backend만 메인과 스왑할 수 있습니다."}
    family = str(clone_spec["account_of"])
    if family not in _SWAPPABLE_FAMILIES:
        return {"error": f"'{family}' 계정 스왑은 아직 지원하지 않습니다(codex만 가능)."}
    original_spec = cfg.backends.get(family)
    if not isinstance(original_spec, dict):
        return {"error": f"원본 backend를 찾을 수 없습니다: {family}"}

    original_dir = Path(_backend_auth_dir(family, original_spec)).expanduser()
    clone_dir = Path(_backend_auth_dir(name, clone_spec)).expanduser()
    if not clone_dir.is_dir():
        return {"error": f"복제 계정 디렉터리가 없습니다: {clone_dir}"}
    if not original_dir.is_dir():
        return {"error": f"원본 계정 디렉터리가 없습니다: {original_dir}"}

    tmp_dir = original_dir.parent / f"{original_dir.name}.yok3x-swap-tmp"
    if tmp_dir.exists():
        return {"error": f"이전 스왑이 남긴 임시 폴더가 있습니다: {tmp_dir} — 수동으로 정리 후 다시 시도하세요."}

    # 3단계 이름바꾸기. Windows는 파일이 열려 있으면(예: codex.exe가 떠 있음) OSError로 거부한다
    # — 프로세스를 찾아 죽이는 대신 그 신호를 그대로 사용자에게 보여준다(무엇을 얼마나 확신
    # 못 하고 껐는지 짐작하지 않는다 — 종료는 사용자 몫).
    try:
        original_dir.rename(tmp_dir)
    except OSError as e:
        return {"error": f"1단계(원본→임시) 실패 — codex.exe/관련 프로세스를 모두 종료한 뒤 다시 시도하세요: {e}",
               "locked": True}      # GUI가 이 신호로 "종료하고 재시도" 버튼을 보여준다
    try:
        clone_dir.rename(original_dir)
    except OSError as e:
        try:
            tmp_dir.rename(original_dir)      # 롤백
        except OSError:
            return {"error": f"2단계 실패했고 롤백도 실패했습니다 — 수동 확인 필요"
                             f"({tmp_dir} ↔ {original_dir}): {e}"}
        return {"error": f"2단계(복제→원본) 실패, 롤백함(원상복구됨): {e}"}
    try:
        tmp_dir.rename(clone_dir)
    except OSError as e:
        return {"error": f"3단계(임시→복제) 실패 — 1·2단계는 이미 적용됐습니다. "
                         f"{tmp_dir}를 수동으로 {clone_dir}로 옮기세요: {e}"}

    limits.clear_cache()
    usage.clear_cache()
    _refresh_gui_state_sync(cfg)
    return {"ok": True, "original_dir": str(original_dir), "clone_dir": str(clone_dir)}


def build_state(cfg: Config) -> dict:
    state_started = time.perf_counter()
    totals = usage.today_totals(cfg)
    tools = []
    # 사용량 패널도 계정 카드·코치와 **같은 목록**을 써야 한다. cfg.backends 전체를 돌면
    # `mock`(드라이런 스텁)·`local`(자체 호스팅)이 0%로 상주하고(사용자 지적), 무엇보다
    # 쓸모없는 프로브를 2개 더 돌려 build_state를 느리게 만든다.
    for b in usage.limits_backend_names(cfg):
        probe_started = time.perf_counter()
        v = usage.check_backend(cfg, b, probe_fn=limits.probe_gui)
        probe_ms = (time.perf_counter() - probe_started) * 1000
        if probe_ms >= 2000:
            _GUI_LOGGER.warning(
                "slow backend check backend=%s duration_ms=%.1f level=%s source=%s error=%s",
                b, probe_ms, v.level, v.source, v.reading.error if v.reading else "")
        t = totals.get(b, {"usd": 0, "tokens": 0, "calls": 0})
        wins = [{"name": w.name, "used_percent": round(w.used_percent, 1),
                 "reset": w.reset_in(), "used_tokens": w.used_tokens,
                 "limit_tokens": w.limit_tokens}
                for w in (v.reading.windows if v.reading else [])]
        pace = None
        if v.reading and v.real:              # 실측 7d 있을 때만 하루 페이싱 상태 표시
            _ra = usage.effective_reset_at(cfg, b, v.reading)   # 캐시 폴백(oauth 플랩 대비)
            _cur, _known, _tu = usage._pace_inputs(cfg, b, v.reading, _ra)
            ps = usage.daily_pace_status(cfg, b, _cur,
                                         today=usage._pacing_day_key(_ra), reset_at=_ra,
                                         today_used=_tu, since_reset_known=_known)
            if ps:
                pace = {"used": round(ps["used"], 1), "cap": round(ps["cap"], 1),
                        "soft": round(ps["soft"], 1), "base_cap": round(ps["base_cap"], 1),
                        "strategy": ps["strategy"],
                        "start": round(ps["current"] - ps["used"], 1),  # 오늘 아침 7d 기준선
                        "forward_daily": ps.get("forward_daily"),       # 이후 지속가능 일일률
                        "even_cap": ps.get("even_cap"),                 # 엄격 균등선 여유(오버레이 전용)
                        "level": ps["level"], "blocked": ps["blocked"], "approved": ps["approved"]}
        tools.append({
            "backend": b, "level": v.level, "ratio": round(v.ratio, 3),
            "source": v.source, "real": v.real, "detail": v.detail,
            "windows": wins, "calls": t.get("calls", 0),
            "usd": round(t.get("usd", 0), 2), "tokens": t.get("tokens", 0),
            "pace": pace,
        })
    g = cfg.yok3x["guard"]
    workers = {name: {"backend": w.get("backend"), "role": w.get("role", ""),
                      "model": w.get("model", ""), "effort": w.get("effort", "")}
               for name, w in cfg.yok3x.get("workers", {}).items()}
    result = {
        "version": __version__,   # 코드 버전(단일 출처) — 저장된 yok3x.json의 낡은 값에 오염되지 않게
        "flavor": cfg.yok3x["flavor"],
        "flavors": list(cfg.yok3x.get("flavors", {})),
        "workspace": cfg.yok3x.get("workspace", ""),
        "active_profile": cfg.yok3x.get("active_profile", ""),
        "plan": ((cfg.yok3x.get("limits") or {}).get("claude") or {}).get("plan", ""),
        # claude 토큰 자동갱신 on/off + 토큰 상태(읽기만 — 여기서 refresh 트리거 금지)
        "claude_auto_refresh": bool(((cfg.yok3x.get("limits") or {}).get("claude") or {})
                                    .get("auto_refresh", False)),
        "claude_autocalibrate": bool(((cfg.yok3x.get("limits") or {}).get("claude") or {})
                                      .get("autocalibrate", True)),
        "claude_autocalibrate_reason": ((usage._load_pace(cfg).get("claude") or {})
                                         .get("calib_stop_reason", "")),
        "automation_mode": cfg.yok3x.get("automation_mode", "off"),
        "automation_options": cfg.yok3x.get("automation", {}),
        "claude_token": limits.claude_token_status(
            (cfg.yok3x.get("limits") or {}).get("claude") or {}),
        "profiles": list(cfg.yok3x.get("profiles", {})),
        "route_preview": _routing_preview(cfg),
        # Profile routes are configuration-only; availability is already
        # represented by the async route preview above.
        "profile_routes": _profile_routes(cfg),
        "models_catalog": cfg.yok3x.get("models_catalog", {}),
        "backend_models": {b: limits.list_models_gui(cfg, b) for b in ("claude", "codex", "gemini")},
        # effort 미지정 시 실제 적용되는 각 CLI의 기본값(알 수 있는 것만. 모르면 "")
        "effort_defaults": backends.effort_defaults(),
        "default_effort": cfg.yok3x.get("default_effort", ""),   # yok3x 전역 기본(있으면 이게 우선)
        "guard": {"enabled": g.get("enabled", True),
                  "soft": g.get("soft_ratio", 0.8), "hard": g.get("hard_ratio", 1.0),
                  "failover": bool((g.get("degrade") or {}).get("failover_enabled", False)),
                  "switch_before_degrade": bool((g.get("degrade") or {}).get("switch_before_degrade", True)),
                  "failover_min_gain": float((g.get("degrade") or {}).get("failover_min_gain", 0.1)),
                  "offline": bool((g.get("degrade") or {}).get("offline_enabled", True)),
                  "pace": {"enabled": bool((g.get("daily_pace") or {}).get("enabled", False)),
                           "cap_pct": round(float((g.get("daily_pace") or {}).get("pct_of_weekly", 0.14)) * 100),
                           "mode": (g.get("daily_pace") or {}).get("mode", "warn"),
                           "strategy": (g.get("daily_pace") or {}).get("strategy", "fixed"),
                           "underuse_policy": (g.get("daily_pace") or {}).get("underuse_policy", "flat"),
                           "overuse_policy": (g.get("daily_pace") or {}).get("overuse_policy", "flat")}},
        "coach": usage.coach_messages(cfg, probe_fn=limits.probe_gui),
        "runs": _recent_runs(cfg, int(cfg.yok3x.get("runs_max", 20))),   # 작업별 그룹핑 위해 히스토리↑
        "tools": tools,
        "running": dict(_RUN_STATE),
        "queue": [Path(t).name for t, _ in _QUEUE],
        "tasks": _list_tasks(cfg),
        "saved_tasks": _saved_tasks(cfg),   # [{name,label}] — 작업별 보기와 통합용
        "workers": workers,
        "backends": list(cfg.backends),
        "backend_accounts": _backend_account_rows(cfg),
        # 화면 배치 설정(계정 카드 순서 등). 이걸 안 내려주면 GUI가 저장한 순서를 새로고침 때
        # 되읽지 못해 매번 기본 순서로 돌아간다.
        "gui": dict(cfg.yok3x.get("gui", {})),
        "routing": dict(cfg.yok3x.get("routing", {})),
    }
    state_ms = (time.perf_counter() - state_started) * 1000
    if state_ms >= 2000:
        _GUI_LOGGER.warning("slow build_state duration_ms=%.1f", state_ms)
    return result


def _gui_state(cfg: Config, *, copy_snapshot: bool = True) -> dict:
    """Serve the last snapshot while a full state build runs off the HTTP thread.

    Callers that will only serialize the result may disable the defensive copy;
    the HTTP handler does that because snapshots are replaced, never mutated,
    by the refresh thread.
    """
    global _GUI_STATE, _GUI_STATE_BUILT_AT, _GUI_STATE_REFRESHING
    now = time.time()
    with _GUI_STATE_GUARD:
        if (not _GUI_STATE_REFRESHING
                and (_GUI_STATE is None or now - _GUI_STATE_BUILT_AT >= _GUI_STATE_REFRESH_SEC)):
            _GUI_STATE_REFRESHING = True

            def refresh() -> None:
                global _GUI_STATE, _GUI_STATE_BUILT_AT, _GUI_STATE_REFRESHING
                try:
                    state = build_state(cfg)
                    with _GUI_STATE_GUARD:
                        _GUI_STATE = state
                        _GUI_STATE_BUILT_AT = time.time()
                    _GUI_LOGGER.info("GUI state snapshot refreshed")
                except Exception:
                    _GUI_LOGGER.exception("GUI state snapshot refresh failed")
                finally:
                    with _GUI_STATE_GUARD:
                        _GUI_STATE_REFRESHING = False
                        _GUI_STATE_GUARD.notify_all()

            threading.Thread(target=refresh, name="gui-state-refresh", daemon=True).start()
        if _GUI_STATE is not None:
            return copy.deepcopy(_GUI_STATE) if copy_snapshot else _GUI_STATE
    return {
        "version": __version__, "flavor": cfg.yok3x.get("flavor", ""),
        "tools": [], "running": dict(_RUN_STATE), "queue": [], "tasks": [],
        "backends": list(cfg.backends), "state_status": "warming",
    }


def _refresh_gui_state_sync(cfg: Config) -> None:
    """Publish a fresh snapshot after a rare, user-initiated state change."""
    global _GUI_STATE, _GUI_STATE_BUILT_AT, _GUI_STATE_REFRESHING
    with _GUI_STATE_GUARD:
        while _GUI_STATE_REFRESHING:
            _GUI_STATE_GUARD.wait()
        _GUI_STATE_REFRESHING = True
    try:
        state = build_state(cfg)
        with _GUI_STATE_GUARD:
            _GUI_STATE = state
            _GUI_STATE_BUILT_AT = time.time()
        _GUI_LOGGER.info("GUI state snapshot refreshed synchronously")
    finally:
        with _GUI_STATE_GUARD:
            _GUI_STATE_REFRESHING = False
            _GUI_STATE_GUARD.notify_all()


def _list_tasks(cfg: Config) -> list:
    try:
        return sorted(p.name for p in cfg.paths.root.glob("task-*.json"))
    except OSError:
        return []


def _saved_tasks(cfg: Config) -> list:
    """저장된 작업 = [{name, label}]. label(작업 이름)으로 '작업별 보기'와 런을 통합한다."""
    out = []
    try:
        for p in sorted(cfg.paths.root.glob("task-*.json")):
            label = p.stem[len("task-"):]           # 파일명 기본
            try:
                spec = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(spec, dict) and str(spec.get("label", "")).strip():
                    label = str(spec["label"]).strip()
            except (OSError, json.JSONDecodeError):
                pass
            out.append({"name": p.name, "label": label})
    except OSError:
        pass
    return out


def _recent_runs(cfg: Config, n: int = 6) -> list:
    runs = []
    rd = cfg.paths.runs
    if not rd.exists():
        return runs
    for d in sorted(rd.iterdir(), reverse=True)[:n]:
        st = d / "status.json"
        if not st.exists():
            continue
        try:
            data = json.loads(st.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        steps = data.get("steps", [])
        # v4.6.0 S9: sync_layer가 켜진 런만 understanding_bundle.json이 있다(기본 off이므로
        # 대다수 런은 없음 — 없으면 조용히 생략, GUI는 claim 영역 자체를 숨긴다).
        understanding = None
        try:
            raw_bundle = json.loads((d / "understanding_bundle.json").read_text(encoding="utf-8"))
            understanding = {"claims": raw_bundle.get("claims") or []}
        except (OSError, json.JSONDecodeError):
            pass
        runs.append({
            "run_id": data.get("run_id"), "pattern": data.get("pattern"),
            "state": data.get("state"), "task": data.get("task", ""),
            "label": (data.get("label") or "").strip(),   # 작업 그룹 라벨(없으면 GUI가 무제목 처리)
            "steps": len(steps),
            "done": sum(1 for s in steps if s.get("status") == "done"),
            "last": steps[-1]["worker"] if steps else "-",
            "score": next((s.get("score") for s in reversed(steps)
                           if s.get("score") is not None), None),
            "stepdetail": [{"index": s.get("index"), "worker": s.get("worker"),
                            "kind": s.get("task_kind"), "status": s.get("status"),
                            "score": s.get("score"), "summary": (s.get("summary") or "")[:160],
                            "issues": s.get("checklist", []),
                            # 관찰가능성(A-lite): 스텝별 계측. None이면 GUI가 '—'로 표시
                            "tokens": s.get("tokens"), "cost_usd": s.get("cost_usd"),
                            "duration_ms": s.get("duration_ms")} for s in steps[-8:]],
            "understanding": understanding,
            "automation_decision": data.get("automation_decision"),
        })
    return runs


# ---------------------------------------------------------------- 실행 큐

def _run_bg(cfg: Config, tf: str, iterations: int) -> None:
    from . import orchestrator
    name = Path(tf).name
    try:
        if iterations > 1:
            orchestrator.run_loop(cfg, tf, iterations=iterations, auto=True)
            last = f"{name} → loop×{iterations} 종료"
        else:
            state = orchestrator.run_task_file(cfg, tf, auto=True)
            last = f"{name} → {state}"
    except Exception as e:  # 삼키지 않고 상태에 기록
        last = f"{name} → error: {type(e).__name__}: {e}"
    with _LOCK:
        _RUN_STATE.update({"active": False, "task": None, "since": None, "last": last})
    _start_next(cfg)


def _start_next(cfg: Config) -> None:
    with _LOCK:
        if _RUN_STATE["active"] or not _QUEUE:
            return
        tf, iters = _QUEUE.pop(0)
        _RUN_STATE.update({"active": True, "task": Path(tf).name,
                           "since": datetime.now().strftime("%H:%M:%S")})
    threading.Thread(target=_run_bg, args=(cfg, tf, iters), daemon=True).start()


def _enqueue(cfg: Config, tf: str, iterations: int) -> dict:
    with _LOCK:
        _QUEUE.append((tf, iterations))
        pos = len(_QUEUE)
        busy = _RUN_STATE["active"]
    _start_next(cfg)
    return {"ok": True, "queued": busy, "position": pos}


import re as _re

# task-<slug>.json — 유니코드 단어문자 허용(한글 작업명). [\w-]는 / \ . 를 포함하지 않아
# 경로순회(../, 슬래시)가 원천 차단된다(+_task_path에서 parent==root 재확인).
_TASK_NAME_RE = _re.compile(r"^task-[\w-]+\.json$", _re.UNICODE)
_VALID_PATTERNS = ("producer-reviewer", "pipeline", "fanout", "fanout-fanin")
_VALID_SCORE_GATE_MODES = ("strict", "advisory")


def _slug_task_name(raw: str) -> str:
    """사용자 입력 이름 → task-<slug>.json. 공백·구두점→하이픈, 유니코드 단어문자(한글 등) 유지."""
    s = _re.sub(r"[^\w-]+", "-", str(raw or "").strip().lower(), flags=_re.UNICODE).strip("-_")
    return f"task-{s}.json" if s else ""


def _task_path(cfg: Config, name: str) -> Path | None:
    """검증된 task 파일 경로. 형식 위반·경로순회(root 밖)면 None(보안)."""
    if not isinstance(name, str) or not _TASK_NAME_RE.match(name):
        return None
    root = cfg.paths.root.resolve()
    p = (cfg.paths.root / name).resolve()
    if p.parent != root:                    # root 바로 아래만 허용(../ 등 차단)
        return None
    return p


def _validate_task_spec(spec: dict, cfg: Config | None = None,
                        allow_draft: bool = False) -> str:
    if not isinstance(spec, dict):
        return "spec이 객체가 아님"
    if not allow_draft and not str(spec.get("task", "")).strip():
        return "task(목표)가 비었다"
    if spec.get("pattern") not in _VALID_PATTERNS:
        return "pattern이 잘못됨"
    changes = spec.get("changes")
    if changes is not None:
        if not isinstance(changes, dict):
            return "changes가 객체가 아님"
        if changes.get("apply_mode", "review") not in APPLY_MODES:
            return f"changes.apply_mode가 잘못됨 ({'/'.join(APPLY_MODES)})"
    try:
        validate_task_automation_mode(spec)
    except ValueError as exc:
        return str(exc)
    gate_mode = spec.get("score_gate_mode", "strict")
    if gate_mode not in _VALID_SCORE_GATE_MODES:
        return "score_gate_mode가 잘못됨(strict/advisory)"
    effective_verify_cmd = (spec.get("verify_cmd")
                            or ((cfg.yok3x.get("verify_cmd", "") or "") if cfg else ""))
    if (spec.get("pattern") == "producer-reviewer"
            and gate_mode == "advisory" and not str(effective_verify_cmd).strip()):
        return "score_gate_mode=advisory에는 verify_cmd가 필요함"
    mat = spec.get("materialize")
    if mat is not None:
        if not isinstance(mat, dict):
            return "materialize가 객체가 아님"
        if "root" in mat and mat["root"] is not None and not isinstance(mat["root"], str):
            return "materialize.root는 문자열이어야 함"
    agents = spec.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            return "agents가 객체가 아님"
        if cfg is None:
            return "agents 검증에 config가 필요함"
        workers = cfg.yok3x.get("workers", {})
        for worker, override in agents.items():
            if worker not in workers:
                return f"없는 워커(agents): {worker}"
            if not isinstance(override, dict):
                return f"agents.{worker}가 객체가 아님"
            if "backend" in override and override["backend"] not in cfg.backends:
                return f"잘못된 backend(agents.{worker}): {override['backend']}"
            effort = override.get("effort")
            if effort and str(effort) not in EFFORTS_OK:
                return (f"effort 값 오류(agents.{worker}): {effort} "
                        f"({'/'.join(EFFORTS_OK)})")
    return ""


def _save_task(cfg: Config, raw_name: str, spec: dict, create_only: bool = False) -> dict:
    name = _slug_task_name(raw_name)
    if not name:
        return {"error": "이름이 비었거나 유효한 문자가 없다(영소문자·숫자·하이픈)"}
    p = _task_path(cfg, name)
    if p is None:
        return {"error": "잘못된 작업 이름/경로"}
    if create_only and p.exists():
        return {"error": "같은 이름의 작업이 이미 있다"}
    err = _validate_task_spec(spec, cfg, allow_draft=True)
    if err:
        return {"error": err}
    spec.setdefault("label", raw_name.strip())   # 라벨 기본=사용자 이름(작업별 콘솔 연동)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)                                # 원자적 교체
    return {"ok": True, "name": name}


def _load_task(cfg: Config, name: str) -> dict:
    p = _task_path(cfg, name)
    if p is None or not p.exists():
        return {"error": "없는 작업"}
    try:
        return {"ok": True, "name": name, "spec": json.loads(p.read_text(encoding="utf-8-sig"))}
    except Exception as e:
        return {"error": f"JSON 파싱 실패: {e}"}


def _delete_task(cfg: Config, name: str) -> dict:
    p = _task_path(cfg, name)
    if p is None or not p.exists():
        return {"error": "없는 작업"}
    try:
        p.unlink()
    except OSError as e:
        return {"error": f"삭제 실패: {e}"}
    return {"ok": True}


def _rename_task(cfg: Config, old_name: str, new_raw_name: str) -> dict:
    """작업 파일과 label을 함께 변경한다. 새 파일 저장 성공 전에는 원본을 건드리지 않는다."""
    old_path = _task_path(cfg, old_name)
    if old_path is None or not old_path.exists():
        return {"error": "없는 작업"}

    new_label = str(new_raw_name or "").strip()
    new_name = _slug_task_name(new_label)
    if not new_name:
        return {"error": "이름이 비었거나 유효한 문자가 없다(영소문자·숫자·하이픈)"}
    new_path = _task_path(cfg, new_name)
    if new_path is None:
        return {"error": "잘못된 작업 이름/경로"}
    # 자기 자신으로의 이름 변경(같은 slug)은 충돌이 아니다 — 프롬프트가 현재 이름을 미리 채워주므로
    # 그대로 확인만 눌러도 여기 걸려 "이미 있다"가 뜨던 버그. 이 경우 파일 이동 없이 label만 갱신한다
    # (예: "My Task"→"my task"처럼 표시만 바꾸는 정상 케이스도 지원).
    same_file = new_path == old_path
    if new_path.exists() and not same_file:
        return {"error": "같은 이름의 작업이 이미 있다"}

    loaded = _load_task(cfg, old_name)
    if not loaded.get("ok"):
        return loaded
    if not isinstance(loaded.get("spec"), dict):
        return {"error": "spec이 객체가 아님"}
    spec = dict(loaded["spec"])
    spec["label"] = new_label
    tmp = new_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(new_path)                 # 새 파일을 먼저 완성해 원본 유실 방지
    except (OSError, TypeError, ValueError) as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return {"error": f"이름 변경 저장 실패: {e}"}

    if same_file:                             # 같은 파일에 label만 갱신 — 지울 원본이 없다
        return {"ok": True, "name": new_name, "label": new_label}
    try:
        old_path.unlink()                     # 새 파일 저장 성공 뒤에만 원본 삭제
    except OSError as e:
        try:
            new_path.unlink()                 # 가능한 한 변경 전 상태로 되돌림
        except OSError:
            pass
        return {"error": f"이름 변경 삭제 실패: {e}"}
    return {"ok": True, "name": new_name, "label": new_label}


_DRAFT_RUN_ERROR = "목표가 비었다 — 작업을 열어 목표를 입력하라"


def _saved_task_for_run(cfg: Config, name: str) -> tuple[Path | None, str]:
    """등록 작업 실행 직전 draft 여부를 확인한다. 기존 정상 작업의 실행 형식은 유지한다."""
    p = _task_path(cfg, name)
    if p is None or not p.exists() or name not in _list_tasks(cfg):
        return None, "unknown task"
    try:
        spec = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return p, ""                         # 기존처럼 실행부가 원래 오류를 보고하게 둔다
    if isinstance(spec, dict):
        if not str(spec.get("task", "")).strip():
            return None, _DRAFT_RUN_ERROR
        err = _validate_task_spec(spec, cfg)
        if err:
            return None, err
    return p, ""


def _enqueue_saved_task(cfg: Config, name: str, iterations: int) -> dict:
    """등록 작업을 검증한 뒤에만 실행 큐에 넣는다."""
    p, err = _saved_task_for_run(cfg, name)
    if err:
        return {"error": err}
    return _enqueue(cfg, str(p), iterations)


def _write_inline_spec(cfg: Config, spec: dict) -> Path:
    spec.setdefault("label", "")   # 인라인은 label 키를 명시(없으면 무제목 — 임시파일명 폴백 방지)
    d = cfg.paths.yok3x_dir / "console"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    tf = d / f"task-console-{ts}.json"
    tf.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return tf


# ---------------------------------------------------------------- v4.6.0 Cognitive Sync Layer S6a

def _sync_claim_action(cfg: Config, body: dict) -> dict:
    """온디맨드(§3.6 사용자 제안): claim 하나를 골라 `explain`/`quiz` 1회만 호출한다.

    자동 트리거가 아니다 — 사용자가 GUI에서 실제로 클릭했을 때만 이 경로를 탄다. 가드(요금
    한도)·원장 기록은 일반 워커 호출과 동일하게 적용(전용 경로라고 우회하지 않음)."""
    from . import review as _review
    run_id = str(body.get("run_id", "")).strip()
    claim_id = str(body.get("claim_id", "")).strip()
    action = str(body.get("action", "")).strip()
    if action not in ("explain", "quiz"):
        return {"ok": False, "error": "action은 explain 또는 quiz만 허용"}
    if not _review._safe_run_id(run_id):
        return {"ok": False, "error": f"잘못된 run_id: {run_id!r}"}
    if not claim_id:
        return {"ok": False, "error": "claim_id 필요"}

    bundle_path = cfg.paths.runs / run_id / "understanding_bundle.json"
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except OSError:
        return {"ok": False, "error": f"understanding_bundle 없음(run_id={run_id}) — "
                                      "그 런에서 sync_layer.enabled가 꺼져 있었을 수 있음"}
    except json.JSONDecodeError:
        return {"ok": False, "error": "understanding_bundle 파싱 실패"}

    claim = next((c for c in (bundle.get("claims") or []) if c.get("claim_id") == claim_id), None)
    if claim is None:
        return {"ok": False, "error": f"claim_id 없음: {claim_id}"}

    sl_cfg = cfg.yok3x.get("sync_layer") or {}
    backend_name = sl_cfg.get("on_demand_backend") or "claude"
    if backend_name not in cfg.backends:
        return {"ok": False, "error": f"backend 없음: {backend_name}"}
    verdict = usage.check_backend(cfg, backend_name)
    if verdict.level == "stop":
        return {"ok": False, "error": f"요금 가드 정지: {backend_name} {verdict.metric} "
                                      f"{verdict.ratio:.0%} ({verdict.detail})"}

    prompt = (sync_layer.explain_claim_prompt if action == "explain"
             else sync_layer.quiz_claim_prompt)(claim)
    res = backends.run_backend(backend_name, cfg.backends[backend_name], prompt)
    usage.record(cfg, "sync_layer", action, res, run_id=run_id)
    if not res.ok:
        return {"ok": False, "error": res.error or "호출 실패"}
    return {"ok": True, "action": action, "claim_id": claim_id, "text": res.text,
           "cost_usd": res.cost_usd, "tokens": res.total_tokens}


# ---------------------------------------------------------------- config 편집

def _apply_config(cfg: Config, body: dict) -> dict:
    """워커 backend·routing·flavor 부분 갱신 → 검증 후 yok3x.json 저장.

    검증 실패는 저장하지 않고 사유 반환(조용한 폴백 금지). 저장 전 .bak 백업.
    """
    workers = body.get("workers") or {}
    routing = body.get("routing") or {}
    flavor = body.get("flavor")
    for w, be in workers.items():
        if w not in cfg.yok3x.get("workers", {}):
            return {"error": f"없는 워커: {w}"}
        if be not in cfg.backends:
            return {"error": f"잘못된 backend: {be} (가능: {', '.join(cfg.backends)})"}
    for fn, be in routing.items():
        if be not in cfg.backends:
            return {"error": f"routing '{fn}' backend 잘못됨: {be}"}
    if flavor is not None and flavor not in cfg.yok3x.get("flavors", {}):
        return {"error": f"없는 flavor: {flavor}"}
    workspace = body.get("workspace")
    if workspace is not None:
        ws = str(workspace).strip()
        if ws and not Path(ws).is_dir():
            return {"error": f"workspace 디렉터리 없음(오타?): {ws}"}
    active_profile = body.get("active_profile")
    if active_profile is not None:
        ap = str(active_profile).strip()
        ap = "" if ap == "off" else ap
        if ap and ap not in cfg.yok3x.get("profiles", {}):
            return {"error": f"없는 프로파일: {ap} (가능: {', '.join(cfg.yok3x.get('profiles', {}))}, off)"}
    worker_models = body.get("worker_models") or {}   # 워커별 수동 모델(빈값=기본)
    for w in worker_models:
        if w not in cfg.yok3x.get("workers", {}):
            return {"error": f"없는 워커(model): {w}"}
    worker_efforts = body.get("worker_efforts") or {}  # 워커별 추론 강도(빈값=기본)
    for w, e in worker_efforts.items():
        if w not in cfg.yok3x.get("workers", {}):
            return {"error": f"없는 워커(effort): {w}"}
        if e and str(e) not in EFFORTS_OK:
            return {"error": f"effort 값 오류: {e} (minimal/low/medium/high/xhigh/max)"}
    failover_enabled = body.get("failover_enabled")   # P2 폴오버 on/off
    switch_before_degrade = body.get("switch_before_degrade")
    failover_min_gain = body.get("failover_min_gain")
    if failover_min_gain is not None:
        try:
            failover_min_gain = float(failover_min_gain)
        except (TypeError, ValueError):
            return {"error": f"failover_min_gain 숫자 아님: {failover_min_gain}"}
        if not 0 <= failover_min_gain <= 1:
            return {"error": f"failover_min_gain 범위(0~1) 벗어남: {failover_min_gain}"}
    offline_enabled = body.get("offline_enabled")     # P3 오프라인(로컬) 폴백 on/off
    auto_refresh = body.get("auto_refresh")           # claude 토큰 자체갱신 on/off
    autocalibrate = body.get("autocalibrate")
    automation_mode = body.get("automation_mode")
    automation_options = body.get("automation")
    if automation_mode is not None or "automation" in body:
        candidate = copy.deepcopy(cfg.yok3x)
        if automation_mode is not None:
            try:
                candidate["automation_mode"] = validate_automation_mode(automation_mode)
            except ValueError as exc:
                return {"error": str(exc)}
        if "automation" in body:
            if not isinstance(automation_options, dict):
                return {"error": "automation must be an object"}
            candidate.setdefault("automation", {}).update(automation_options)
        try:
            validate_automation_config(candidate)
        except (TypeError, ValueError) as exc:
            return {"error": str(exc)}
    soft = body.get("soft_ratio")
    hard = body.get("hard_ratio")
    for nm, v in (("soft_ratio", soft), ("hard_ratio", hard)):
        if v is not None:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return {"error": f"{nm} 숫자 아님: {v}"}
            if not (0 < fv <= 2):
                return {"error": f"{nm} 범위(0~2) 벗어남: {fv}"}
    plan = body.get("plan")
    if plan:
        from .config import PLAN_PRESETS
        if plan not in PLAN_PRESETS.get("claude", {}):
            return {"error": f"없는 요금제: {plan} (가능: {', '.join(PLAN_PRESETS.get('claude', {}))})"}
    # 백업 후 적용
    jf = cfg.paths.yok3x_json
    if jf.exists():
        try:
            (jf.parent / (jf.name + ".bak")).write_text(
                jf.read_text(encoding="utf-8-sig"), encoding="utf-8")
        except OSError:
            pass
    for w, be in workers.items():
        cfg.yok3x["workers"][w]["backend"] = be
    for fn, be in routing.items():
        cfg.yok3x.setdefault("routing", {})[fn] = be
    if flavor is not None:
        cfg.yok3x["flavor"] = flavor
    if workspace is not None:
        cfg.yok3x["workspace"] = str(workspace).strip()
    if active_profile is not None:
        ap = str(active_profile).strip()
        cfg.yok3x["active_profile"] = "" if ap == "off" else ap
    for w, m in worker_models.items():
        cfg.yok3x["workers"][w]["model"] = str(m or "").strip()
    for w, e in worker_efforts.items():
        cfg.yok3x["workers"][w]["effort"] = str(e or "").strip()
    if failover_enabled is not None:
        cfg.yok3x.setdefault("guard", {}).setdefault("degrade", {})["failover_enabled"] = bool(failover_enabled)
    if switch_before_degrade is not None:
        cfg.yok3x.setdefault("guard", {}).setdefault("degrade", {})["switch_before_degrade"] = bool(switch_before_degrade)
    if failover_min_gain is not None:
        cfg.yok3x.setdefault("guard", {}).setdefault("degrade", {})["failover_min_gain"] = failover_min_gain
    if offline_enabled is not None:
        cfg.yok3x.setdefault("guard", {}).setdefault("degrade", {})["offline_enabled"] = bool(offline_enabled)
    if auto_refresh is not None:
        cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})["auto_refresh"] = bool(auto_refresh)
    if autocalibrate is not None:
        claude_limits = cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})
        claude_limits["autocalibrate"] = bool(autocalibrate)
        if autocalibrate:
            pace = usage._load_pace(cfg)
            claude_pace = pace.get("claude")
            if isinstance(claude_pace, dict):
                claude_pace.pop("calib_stop_reason", None)
                usage._save_pace(cfg, pace)
    if automation_mode is not None:
        cfg.yok3x["automation_mode"] = automation_mode
    if "automation" in body:
        cfg.yok3x.setdefault("automation", {}).update(automation_options)
    if soft is not None:
        cfg.yok3x["guard"]["soft_ratio"] = float(soft)
    if hard is not None:
        cfg.yok3x["guard"]["hard_ratio"] = float(hard)
    if plan is not None:
        cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})["plan"] = plan
    # 하루 페이싱 설정(enabled/pct_of_weekly/soft_frac/mode)
    dp = body.get("daily_pace")
    if isinstance(dp, dict):
        cur = cfg.yok3x.setdefault("guard", {}).setdefault("daily_pace", {})
        if "enabled" in dp:
            cur["enabled"] = bool(dp["enabled"])
        if dp.get("mode") in ("warn", "pause"):
            cur["mode"] = dp["mode"]
        # BUG-45: "spread"가 이 화이트리스트에 빠져 있어 GUI에서 "분산" 선택이 조용히 무시되고
        # {"ok": true}만 돌아왔다(실측: 클릭해도 서버 값이 안 바뀜, 저장 성공한 것처럼 보임).
        # spread는 usage._pace_cfg가 이미 정식 인식하는 값이라(_daily_cap 분기 존재) 여기 검증만
        # 낡아 있었다 — spread 도입(v3.6.0) 때 이 화이트리스트 갱신을 놓친 것.
        if dp.get("strategy") in ("fixed", "catch_up", "spread", "grace_band", "debt_amortize"):
            cur["strategy"] = dp["strategy"]
        if dp.get("underuse_policy") in usage.UNDERUSE_POLICIES:
            cur["underuse_policy"] = dp["underuse_policy"]
        if dp.get("overuse_policy") in usage.OVERUSE_POLICIES:
            cur["overuse_policy"] = dp["overuse_policy"]
        for k in ("pct_of_weekly", "soft_frac"):
            if k in dp:
                try:
                    v = float(dp[k])
                except (TypeError, ValueError):
                    return {"error": f"daily_pace.{k} 숫자 아님: {dp[k]}"}
                if not 0.0 < v <= 1.0:
                    return {"error": f"daily_pace.{k}는 0~1 범위: {v}"}
                cur[k] = v
    # 하루 페이싱 정지 승인(오늘 재개)
    pace_approve = body.get("pace_approve")
    if pace_approve:
        if (pace_approve not in cfg.yok3x.get("workers", {})
                and pace_approve not in usage.limits_backend_names(cfg)):
            return {"error": f"알 수 없는 backend(pace_approve): {pace_approve}"}
        usage.pace_approve(cfg, pace_approve)
    # 백엔드 계정 카드가 나열되는 순서(계정군 이름 배열). 화면 배치라 gui.* 아래 둔다.
    # 목록에 없는 계정군은 GUI가 뒤에 원래 순서로 붙이므로, 여기서 '전부 나열'을 요구하지 않는다
    # — 새 backend가 생겨도 순서 설정 때문에 화면에서 사라지지 않게 하기 위함.
    card_order = body.get("backend_card_order")
    if card_order is not None:
        if not isinstance(card_order, list):
            return {"error": "backend_card_order는 배열이어야 합니다"}
        families = {str((spec or {}).get("account_of") or name)
                    for name, spec in (cfg.backends or {}).items()}
        cleaned: list[str] = []
        for fam in card_order:
            fam = str(fam)
            if fam not in families:
                return {"error": f"알 수 없는 계정군(backend_card_order): {fam}"}
            if fam in cleaned:
                return {"error": f"중복된 계정군(backend_card_order): {fam}"}
            cleaned.append(fam)
        cfg.yok3x.setdefault("gui", {})["backend_card_order"] = cleaned
    cfg.save_yok3x()
    _refresh_gui_state_sync(cfg)
    out = {"ok": True}
    if card_order is not None:                       # GUI가 저장 성공을 실제로 확인할 수 있게 에코
        out["backend_card_order"] = cfg.yok3x["gui"]["backend_card_order"]
    return out


# ---------------------------------------------------------------- 서버

def serve(cfg: Config, port: int = 8760, open_browser: bool = True) -> None:
    gui_index = Path(__file__).resolve().parent.parent / "gui" / "index.html"
    if not gui_index.exists():
        print(f"GUI 파일 없음: {gui_index}")
        return
    _configure_gui_logging(cfg)

    class Handler(http.server.BaseHTTPRequestHandler):
        # Keep GUI polling connections short-lived.  A browser/preload layer
        # may open a TCP connection before it sends the request line; keeping
        # that connection alive otherwise makes handle_one_request() include
        # the idle socket read in the apparent request duration.
        protocol_version = "HTTP/1.0"
        # This bounds idle client connections (including a client that sends
        # only part of a request). It does not interrupt build_state(); that
        # work must remain cancellable at its subprocess/network boundaries.
        request_timeout = 15.0

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(self.request_timeout)

        def handle_one_request(self) -> None:
            started = time.perf_counter()
            self._response_code = None
            self._request_started_at = None
            try:
                super().handle_one_request()
            except Exception:
                _GUI_LOGGER.exception("request handler exception path=%s", getattr(self, "path", ""))
                raise
            finally:
                # BaseHTTPRequestHandler reads the request line before it
                # dispatches do_GET/do_POST.  Do not report that idle read as
                # application latency; retain the full duration for the
                # blank-path timeout diagnostic below.
                measured_from = self._request_started_at or started
                duration_ms = (time.perf_counter() - measured_from) * 1000
                request_line = getattr(self, "requestline", "")
                path = request_line.split(" ", 2)[1] if request_line.count(" ") >= 2 else ""
                level = logging.WARNING if duration_ms >= 2000 else logging.INFO
                _GUI_LOGGER.log(level, "request path=%s duration_ms=%.1f status=%s",
                                path, duration_ms, getattr(self, "_response_code", None))

        def parse_request(self) -> bool:
            ok = super().parse_request()
            if ok:
                self._request_started_at = time.perf_counter()
            return ok

        def _send(self, code: int, body, ctype: str) -> None:
            self._response_code = code
            self.close_connection = True
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False),
                       "application/json; charset=utf-8")

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, gui_index.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif path == "/api/state":
                try:
                    self._json(200, _gui_state(cfg, copy_snapshot=False))
                except Exception as e:
                    _GUI_LOGGER.exception("GET /api/state failed")
                    self._json(500, {"error": str(e)})
            elif path == "/api/claude/login/status":
                try:
                    self._json(200, _claude_login_status(cfg))
                except Exception as e:
                    _GUI_LOGGER.exception("GET /api/claude/login/status failed")
                    self._json(500, {"error": str(e)})
            elif path == "/api/task":                 # 저장된 작업 열기(?name=task-x.json)
                import urllib.parse as _up
                q = _up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                r = _load_task(cfg, (q.get("name") or [""])[0])
                self._json(200 if r.get("ok") else 400, r)
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._read_body()
            except Exception as e:
                _GUI_LOGGER.exception("POST %s bad request", path)
                self._json(400, {"error": f"bad request: {e}"})
                return

            if path == "/api/pickdir":
                p = pick_directory()   # 로컬 네이티브 폴더 대화상자
                self._json(200, {"path": p or ""})
                return

            if path == "/api/claude/login/start":
                r = _claude_login_start()
                self._json(200 if r.get("ok") else 500, r)
                return

            if path == "/api/run":
                iters = max(1, int(body.get("iterations", 1) or 1))
                spec = body.get("spec")
                if spec:  # 인라인 태스크
                    err = _validate_task_spec(spec, cfg)
                    if err:
                        self._json(400, {"error": err})
                        return
                    tf = _write_inline_spec(cfg, spec)
                else:  # 등록된 task 파일
                    task = str(body.get("task", "")).strip()
                    r = _enqueue_saved_task(cfg, task, iters)
                    self._json(200 if r.get("ok") else 400, r)
                    return
                self._json(200, _enqueue(cfg, str(tf), iters))
                return

            if path == "/api/task":                   # 작업 저장/편집(name, spec)
                r = _save_task(cfg, body.get("name", ""), body.get("spec") or {},
                               create_only=bool(body.get("create_only", False)))
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/task/delete":            # 작업 삭제(name)
                r = _delete_task(cfg, body.get("name", ""))
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/task/rename":            # 작업 이름 변경(name, new_name)
                r = _rename_task(cfg, body.get("name", ""), body.get("new_name", ""))
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/config":
                self._json(200, _apply_config(cfg, body))
                return

            if path == "/api/backends/add":
                r = _add_backend_account(cfg, body)
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/backends/remove":
                r = _remove_backend_account(cfg, body)
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/backends/swap":
                r = _swap_backend_account_dirs(cfg, body)
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/backends/kill_codex":
                r = _kill_codex_processes()
                self._json(200 if r.get("ok") else 400, r)
                return

            if path == "/api/sync/claim_action":      # v4.6.0 S6a: 온디맨드 claim 클릭 explain/quiz
                try:
                    r = _sync_claim_action(cfg, body)
                except Exception as e:
                    _GUI_LOGGER.exception("POST /api/sync/claim_action failed")
                    r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                self._json(200 if r.get("ok") else 400, r)
                return

            self._json(404, {"error": "not found"})

        def log_message(self, *a):
            if not a:
                return
            _GUI_LOGGER.info("http " + str(a[0]), *a[1:])

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    _GUI_LOGGER.info("binding GUI server host=127.0.0.1 port=%s", port)
    httpd = Server(("127.0.0.1", port), Handler)
    _GUI_LOGGER.info("GUI server listening host=127.0.0.1 port=%s", port)
    url = f"http://127.0.0.1:{port}/"
    print(f"yok3x gui → {url}   (Ctrl+C 로 종료)")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    stop_reason = "normal_return"
    previous_sigterm_handler = None
    sigterm_registered = False

    def handle_sigterm(signum, _frame) -> None:
        nonlocal stop_reason
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = str(signum)
        stop_reason = f"external_signal:{signal_name}"
        _GUI_LOGGER.warning("external termination signal received signal=%s", signal_name)
        # Preserve termination semantics while allowing finally to record and
        # flush the shutdown reason. Forced termination (for example,
        # taskkill /F) cannot run a Python signal handler.
        raise SystemExit(128 + signum)

    try:
        previous_sigterm_handler = signal.signal(signal.SIGTERM, handle_sigterm)
        sigterm_registered = True
    except (AttributeError, OSError, RuntimeError, ValueError):
        # Signal availability and main-thread restrictions vary by platform
        # and embedding environment. Observability must not prevent startup.
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
        print("\ngui 종료")
    except Exception as exc:
        stop_reason = f"main_loop_exception:{type(exc).__name__}"
        _GUI_LOGGER.exception("GUI server main loop crashed")
        raise
    finally:
        _GUI_LOGGER.info("GUI server stopping (reason=%s)", stop_reason)
        try:
            try:
                httpd.shutdown()
            except Exception:
                _GUI_LOGGER.exception("GUI server shutdown failed (reason=%s)", stop_reason)
                raise
            else:
                _GUI_LOGGER.info("GUI server stopped (reason=%s)", stop_reason)
        finally:
            _flush_gui_logging()
            if sigterm_registered:
                try:
                    signal.signal(signal.SIGTERM, previous_sigterm_handler)
                except (AttributeError, OSError, RuntimeError, ValueError):
                    pass
