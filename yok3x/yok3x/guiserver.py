"""yok3x gui — 로컬 http 서버로 GUI + 실데이터 JSON을 제공(의존성 0).

GUI(gui/index.html)를 브라우저에 띄우고, `/api/state`가 실제 limits/coach/runs를
JSON으로 준다. 프로토타입이지만 목업이 아니라 진짜 yok3x 데이터를 그린다.
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


def build_state(cfg: Config) -> dict:
    totals = usage.today_totals(cfg)
    tools = []
    for b in usage.BACKEND_KEYS:
        v = usage.check_backend(cfg, b)
        t = totals.get(b, {"usd": 0, "tokens": 0, "calls": 0})
        wins = []
        if v.reading and v.reading.windows:
            for w in v.reading.windows:
                wins.append({"name": w.name,
                             "used_percent": round(w.used_percent, 1),
                             "reset": w.reset_in()})
        tools.append({
            "backend": b, "level": v.level, "ratio": round(v.ratio, 3),
            "source": v.source, "real": v.real, "detail": v.detail,
            "windows": wins, "calls": t.get("calls", 0),
            "usd": round(t.get("usd", 0), 2), "tokens": t.get("tokens", 0),
        })
    g = cfg.yok3x["guard"]
    return {
        "version": cfg.yok3x.get("version", "?"),
        "flavor": cfg.yok3x["flavor"],
        "guard": {"enabled": g.get("enabled", True),
                  "soft": g.get("soft_ratio", 0.8), "hard": g.get("hard_ratio", 1.0)},
        "coach": usage.coach_messages(cfg),
        "runs": _recent_runs(cfg),
        "tools": tools,
        "running": dict(_RUN_STATE),
        "tasks": _list_tasks(cfg),
    }


# 실행 상태(GUI에서 태스크 실행 시) — 단일 실행 락으로 동시 실행 방지.
# last: 직전 실행의 결과/오류를 그대로 보존해 GUI에 표시(조용한 실패 금지).
_RUN_STATE = {"active": False, "task": None, "since": None, "last": None}
_RUN_LOCK = threading.Lock()


def _list_tasks(cfg: Config) -> list:
    try:
        return sorted(p.name for p in cfg.paths.root.glob("task-*.json"))
    except OSError:
        return []


def _run_task_bg(cfg: Config, task_file: str) -> None:
    from . import orchestrator
    task_name = Path(task_file).name
    try:
        state = orchestrator.run_task_file(cfg, task_file, auto=True)
        last = f"{task_name} → {state}"
    except Exception as e:  # 오류를 삼키지 않고 상태에 기록해 GUI에 노출
        last = f"{task_name} → error: {type(e).__name__}: {e}"
    _RUN_STATE.update({"active": False, "task": None, "since": None, "last": last})


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
            "state": data.get("state"), "steps": len(steps),
            "done": sum(1 for s in steps if s.get("status") == "done"),
            "last": steps[-1]["worker"] if steps else "-",
            "score": next((s.get("score") for s in reversed(steps)
                           if s.get("score") is not None), None),
            "stepdetail": [{"index": s.get("index"), "worker": s.get("worker"),
                            "status": s.get("status"), "score": s.get("score"),
                            "issues": s.get("checklist", [])} for s in steps[-5:]],
        })
    return runs


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

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, gui_index.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif path == "/api/state":
                try:
                    body = json.dumps(build_state(cfg), ensure_ascii=False)
                    self._send(200, body, "application/json; charset=utf-8")
                except Exception as e:  # API 오류가 서버를 죽이지 않게
                    self._send(500, json.dumps({"error": str(e)}),
                               "application/json; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            path = self.path.split("?")[0]
            if path != "/api/run":
                self._send(404, json.dumps({"error": "not found"}),
                           "application/json; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                task = str(body.get("task", "")).strip()
            except Exception as e:
                self._send(400, json.dumps({"error": f"bad request: {e}"}),
                           "application/json; charset=utf-8")
                return
            tf = cfg.paths.root / task
            if not task or task not in _list_tasks(cfg) or not tf.exists():
                self._send(400, json.dumps({"error": "unknown task"}),
                           "application/json; charset=utf-8")
                return
            with _RUN_LOCK:
                if _RUN_STATE["active"]:
                    self._send(409, json.dumps({"error": "이미 실행 중"}),
                               "application/json; charset=utf-8")
                    return
                _RUN_STATE.update({"active": True, "task": task,
                                   "since": datetime.now().strftime("%H:%M:%S")})
            threading.Thread(target=_run_task_bg, args=(cfg, str(tf)), daemon=True).start()
            self._send(200, json.dumps({"ok": True, "task": task}),
                       "application/json; charset=utf-8")

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
