"""yok3x gui — 로컬 http 서버로 GUI + 실데이터 JSON을 제공(의존성 0).

GUI(gui/index.html)를 브라우저에 띄우고,
  GET  /api/state   실제 limits/coach/runs/큐 상태
  POST /api/run     인라인 태스크 spec 또는 등록된 task 파일 실행(큐)
  POST /api/config  워커 backend·routing·flavor 편집 → yok3x.json 저장(검증+백업)
프로토타입이지만 목업이 아니라 진짜 yok3x 데이터로 동작한다.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from . import usage
from .config import Config

BACKENDS_OK = ("claude", "codex", "gemini", "mock")

# 실행 상태 + 큐. 단일 실행 락으로 동시 실행 방지, 나머지는 큐 대기.
# last: 직전 실행 결과/오류를 보존해 GUI에 노출(조용한 실패 금지).
_RUN_STATE = {"active": False, "task": None, "since": None, "last": None}
_QUEUE: list[tuple[str, int]] = []   # (task_file, iterations)
_LOCK = threading.Lock()


def build_state(cfg: Config) -> dict:
    totals = usage.today_totals(cfg)
    tools = []
    for b in usage.BACKEND_KEYS:
        v = usage.check_backend(cfg, b)
        t = totals.get(b, {"usd": 0, "tokens": 0, "calls": 0})
        wins = [{"name": w.name, "used_percent": round(w.used_percent, 1),
                 "reset": w.reset_in()} for w in (v.reading.windows if v.reading else [])]
        tools.append({
            "backend": b, "level": v.level, "ratio": round(v.ratio, 3),
            "source": v.source, "real": v.real, "detail": v.detail,
            "windows": wins, "calls": t.get("calls", 0),
            "usd": round(t.get("usd", 0), 2), "tokens": t.get("tokens", 0),
        })
    g = cfg.yok3x["guard"]
    workers = {name: {"backend": w.get("backend"), "role": w.get("role", "")}
               for name, w in cfg.yok3x.get("workers", {}).items()}
    return {
        "version": cfg.yok3x.get("version", "?"),
        "flavor": cfg.yok3x["flavor"],
        "flavors": list(cfg.yok3x.get("flavors", {})),
        "guard": {"enabled": g.get("enabled", True),
                  "soft": g.get("soft_ratio", 0.8), "hard": g.get("hard_ratio", 1.0)},
        "coach": usage.coach_messages(cfg),
        "runs": _recent_runs(cfg),
        "tools": tools,
        "running": dict(_RUN_STATE),
        "queue": [Path(t).name for t, _ in _QUEUE],
        "tasks": _list_tasks(cfg),
        "workers": workers,
        "routing": dict(cfg.yok3x.get("routing", {})),
    }


def _list_tasks(cfg: Config) -> list:
    try:
        return sorted(p.name for p in cfg.paths.root.glob("task-*.json"))
    except OSError:
        return []


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
        runs.append({
            "run_id": data.get("run_id"), "pattern": data.get("pattern"),
            "state": data.get("state"), "task": data.get("task", ""),
            "steps": len(steps),
            "done": sum(1 for s in steps if s.get("status") == "done"),
            "last": steps[-1]["worker"] if steps else "-",
            "score": next((s.get("score") for s in reversed(steps)
                           if s.get("score") is not None), None),
            "stepdetail": [{"index": s.get("index"), "worker": s.get("worker"),
                            "kind": s.get("task_kind"), "status": s.get("status"),
                            "score": s.get("score"), "summary": (s.get("summary") or "")[:160],
                            "issues": s.get("checklist", [])} for s in steps[-8:]],
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


def _write_inline_spec(cfg: Config, spec: dict) -> Path:
    d = cfg.paths.yok3x_dir / "console"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    tf = d / f"task-console-{ts}.json"
    tf.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return tf


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
        if be not in BACKENDS_OK:
            return {"error": f"잘못된 backend: {be} (가능: {', '.join(BACKENDS_OK)})"}
    for fn, be in routing.items():
        if be not in BACKENDS_OK:
            return {"error": f"routing '{fn}' backend 잘못됨: {be}"}
    if flavor is not None and flavor not in cfg.yok3x.get("flavors", {}):
        return {"error": f"없는 flavor: {flavor}"}
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
    cfg.save_yok3x()
    return {"ok": True}


# ---------------------------------------------------------------- 서버

def serve(cfg: Config, port: int = 8760, open_browser: bool = True) -> None:
    gui_index = Path(__file__).resolve().parent.parent / "gui" / "index.html"
    if not gui_index.exists():
        print(f"GUI 파일 없음: {gui_index}")
        return

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code: int, body, ctype: str) -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
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
                    self._json(200, build_state(cfg))
                except Exception as e:
                    self._json(500, {"error": str(e)})
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._read_body()
            except Exception as e:
                self._json(400, {"error": f"bad request: {e}"})
                return

            if path == "/api/run":
                iters = max(1, int(body.get("iterations", 1) or 1))
                spec = body.get("spec")
                if spec:  # 인라인 태스크
                    if not str(spec.get("task", "")).strip():
                        self._json(400, {"error": "task(목표)가 비었다"})
                        return
                    if spec.get("pattern") not in ("producer-reviewer", "pipeline",
                                                   "fanout", "fanout-fanin"):
                        self._json(400, {"error": "pattern이 잘못됨"})
                        return
                    tf = _write_inline_spec(cfg, spec)
                else:  # 등록된 task 파일
                    task = str(body.get("task", "")).strip()
                    tfp = cfg.paths.root / task
                    if not task or task not in _list_tasks(cfg) or not tfp.exists():
                        self._json(400, {"error": "unknown task"})
                        return
                    tf = tfp
                self._json(200, _enqueue(cfg, str(tf), iters))
                return

            if path == "/api/config":
                self._json(200, _apply_config(cfg, body))
                return

            self._json(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"yok3x gui → {url}   (Ctrl+C 로 종료)")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ngui 종료")
    finally:
        httpd.shutdown()
