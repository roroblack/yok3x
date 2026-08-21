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
from .config import Config
from .automation import validate_automation_config, validate_automation_mode, validate_task_automation_mode

EFFORTS_OK = ("minimal", "low", "medium", "high", "xhigh", "max")
APPLY_MODES = ("review", "auto_commit")

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


def build_state(cfg: Config) -> dict:
    state_started = time.perf_counter()
    totals = usage.today_totals(cfg)
    tools = []
    for b in usage.BACKEND_KEYS:
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
        if pace_approve not in cfg.yok3x.get("workers", {}) and pace_approve not in usage.BACKEND_KEYS:
            return {"error": f"알 수 없는 backend(pace_approve): {pace_approve}"}
        usage.pace_approve(cfg, pace_approve)
    cfg.save_yok3x()
    _refresh_gui_state_sync(cfg)
    return {"ok": True}


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
