"""limits.py — 실제 구독 한도 조회(요금 가드의 핵심).

'한도는 무조건 지켜야 한다' 원칙: 자체 원장(usage.jsonl) 대신, 각 CLI 도구가
로컬에 남기는 '서버 보고 사용률'을 직접 읽어 진짜 5시간/7일 한도로 가드를 돌린다.

probe 종류 (yok3x.json 의 limits.<backend>.type):
  codex_sessions      ~/.codex/sessions/**/rollout-*.jsonl 의 최신 rate_limits.
                      primary(5h)/secondary(7d)의 used_percent — OpenAI 서버 보고값(진짜 실측).
  claude_transcripts  ~/.claude/projects/**/*.jsonl 의 usage 를 5h/7d 롤링 윈도우로
                      합산해 설정한 상한(cap) 대비 사용률을 추정.
  command             임의 외부 도구(ccusage / tokscale / CodexBar export 등)를 실행해
                      JSON 응답에서 사용률 필드를 뽑는 범용 어댑터.
  ledger / (미설정)   probe 없음 → usage.py 의 자체 일일 예산으로 폴백.

CodexBar(github.com/steipete/CodexBar)가 macOS에서 `codex /status`·`claude /usage`를
읽어 하는 일을, 파일 직접 파싱으로 크로스플랫폼(윈도우 포함)하게 재현한 것이다.
경로는 Path.home() 기반이라 %USERPROFILE% 를 그대로 따른다.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config


@dataclass
class Window:
    name: str                       # "5h" | "7d" | 등
    used_percent: float             # 0~100 (100 초과 가능)
    resets_at: float | None = None  # epoch seconds(절대 시각) — codex는 실제 리셋 시각
    window_minutes: int | None = None

    def reset_in(self) -> str:
        if self.resets_at:
            secs = int(self.resets_at - time.time())
            if secs <= 0:
                return "리셋 임박"
            h, m = divmod(secs // 60, 60)
            if h >= 24:
                d, hh = divmod(h, 24)
                return f"{d}일 {hh}시간 후"
            return f"{h}시간 {m}분 후"
        if self.window_minutes:
            return f"{self.window_minutes // 60}시간 롤링"
        return "-"


@dataclass
class LimitReading:
    backend: str
    source: str                     # codex_sessions | claude_transcripts | command | ledger | disabled | none
    ok: bool                        # 사용률을 신뢰성 있게 얻었는가
    real: bool                      # 서버 보고 실측이면 True, 롤링 추정이면 False
    windows: list[Window] = field(default_factory=list)
    detail: str = ""
    error: str = ""

    def ratio(self) -> float:
        """가장 높은 윈도우 사용률(0~1). 한도는 '가장 빡빡한 창'을 기준으로 지킨다."""
        return max((w.used_percent for w in self.windows), default=0.0) / 100.0

    def worst(self) -> Window | None:
        return max(self.windows, key=lambda w: w.used_percent, default=None)


# ---------------------------------------------------------------- cache

_CACHE: dict[str, tuple[float, LimitReading]] = {}
_TTL_SEC = 15.0   # 루프 한 바퀴 내 여러 호출이 app-server를 반복 스폰하지 않도록 캐시


def probe(cfg: Config, backend: str, use_cache: bool = True) -> LimitReading:
    if use_cache:
        hit = _CACHE.get(backend)
        if hit and (time.time() - hit[0]) < _TTL_SEC:
            return hit[1]
    r = _probe_uncached(cfg, backend)
    _CACHE[backend] = (time.time(), r)
    return r


def clear_cache() -> None:
    _CACHE.clear()


_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODELS_TTL = 300.0   # 5분: 모델 목록은 자주 안 바뀜


def list_models(cfg: Config, backend: str) -> list[str]:
    """backend별 '사용 가능 모델'을 동적으로 조회(하드코딩 아님). 5분 캐시.

    claude: Anthropic `/v1/models`(구독 OAuth 토큰) · codex: `~/.codex/models_cache.json`의
    slug · gemini: 로컬 캐시·키 접근 불가 → 빈 목록(GUI에서 커스텀 입력). 실패 시 빈 목록.
    """
    hit = _MODELS_CACHE.get(backend)
    if hit and (time.time() - hit[0]) < _MODELS_TTL:
        return hit[1]
    try:
        models = _fetch_models(cfg, backend)
    except Exception:
        models = []
    _MODELS_CACHE[backend] = (time.time(), models)
    return models


def _fetch_models(cfg: Config, backend: str) -> list[str]:
    if backend == "claude":
        conf = (cfg.yok3x.get("limits") or {}).get("claude") or {}
        token, _ = _claude_oauth_token(conf)
        if not token:
            return []
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"authorization": f"Bearer {token}",
                     "anthropic-version": "2023-06-01",
                     "anthropic-beta": conf.get("oauth_beta", "oauth-2025-04-20"),
                     "User-Agent": conf.get("user_agent", "claude-cli/2.1 (external, cli)")})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    if backend == "codex":
        p = Path.home() / ".codex" / "models_cache.json"
        if not p.exists():
            return []
        d = json.loads(p.read_text(encoding="utf-8"))
        return [m.get("slug") for m in d.get("models", [])
                if m.get("slug") and m.get("visibility") != "hide"]
    return []   # gemini 등: 로컬 목록/키 접근 불가 → GUI 커스텀 입력


def _probe_uncached(cfg: Config, backend: str) -> LimitReading:
    conf = (cfg.yok3x.get("limits") or {}).get(backend) or {}
    if conf.get("enabled") is False:
        return LimitReading(backend, "disabled", ok=False, real=False,
                            detail="probe 비활성(ledger 폴백)")
    typ = conf.get("type", "ledger")
    try:
        if typ == "codex_appserver":
            return _probe_codex_appserver(backend, conf)
        if typ == "codex_sessions":
            return _probe_codex_sessions(backend, conf)
        if typ == "claude_oauth":
            return _probe_claude_oauth(backend, conf)
        if typ == "claude_transcripts":
            return _probe_claude_transcripts(backend, conf)
        if typ == "command":
            return _probe_command(backend, conf)
        return LimitReading(backend, "ledger", ok=False, real=False,
                            detail="probe 미설정(ledger 폴백)")
    except Exception as e:  # probe 자체가 절대 크래시로 가드를 무력화하지 않게
        return LimitReading(backend, typ, ok=False,
                            real=typ.startswith("codex_"),
                            error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- codex (라이브 실측: app-server RPC)

def _window_name(mins: int | None) -> str:
    """windowDurationMins → 창 이름. 300→'5h', 10080→'7d', 그 외 시간/일 단위."""
    if not mins:
        return "?"
    if mins == 300:
        return "5h"
    if mins == 10080:
        return "7d"
    if mins % 1440 == 0:
        return f"{mins // 1440}d"
    if mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


def _probe_codex_appserver(backend: str, conf: dict[str, Any]) -> LimitReading:
    """`codex app-server` JSON-RPC 로 '지금 이 순간' 5h/7d 사용률을 조회(진짜 실시간).

    실패하면 세션 파일(stale)로, 그것도 실패하면 ok=False(ledger 폴백)로 내려간다.
    CodexBar 가 쓰는 것과 같은 경로: initialize → account/rateLimits/read.
    """
    exe = shutil.which(conf.get("codex_bin", "codex")) or conf.get("codex_bin", "codex")
    args = conf.get("app_server_args", ["-s", "read-only", "-a", "untrusted", "app-server"])
    timeout = float(conf.get("timeout_sec", 15))
    try:
        rl = _appserver_rate_limits(exe, list(args), timeout)
    except Exception as e:
        rl = None
        live_err = f"{type(e).__name__}: {e}"
    else:
        live_err = "app-server 응답에 rateLimits 없음"
    if rl:
        windows: list[Window] = []
        # 창 이름은 primary/secondary '위치'가 아니라 windowDurationMins '길이'로 유도한다.
        # codex는 상황에 따라 primary에 5h 또는 7d(10080분)를 담아, 위치 고정 매핑이면 오라벨된다.
        for key in ("primary", "secondary"):
            seg = rl.get(key) or {}
            up = seg.get("usedPercent")
            if up is None:
                continue
            mins = _int(seg.get("windowDurationMins"))
            windows.append(Window(name=_window_name(mins), used_percent=float(up),
                                  resets_at=_num(seg.get("resetsAt")),
                                  window_minutes=mins))
        if windows:
            plan = rl.get("planType") or "?"
            det = " · ".join(f"{w.name} {w.used_percent:.0f}%" for w in windows)
            return LimitReading(backend, "codex_appserver", ok=True, real=True,
                                windows=windows, detail=f"plan={plan} {det} (live)")
    # 라이브 실패 → 세션 파일(stale) 폴백
    stale = _probe_codex_sessions(backend, conf)
    if stale.ok:
        stale.detail += "  ⚠stale(파일)"
        stale.error = f"live 실패({live_err}) → 세션 파일 사용"
        return stale
    return LimitReading(backend, "codex_appserver", ok=False, real=True,
                        error=f"live/파일 모두 실패: {live_err}; {stale.error}")


def _appserver_rate_limits(exe: str, args: list[str], timeout: float) -> dict | None:
    proc = subprocess.Popen([exe] + args,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    responses: dict[int, Any] = {}
    got = threading.Event()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and "id" in d and "result" in d:
                responses[d["id"]] = d["result"]
                if d["id"] == 2:
                    got.set()

    threading.Thread(target=reader, daemon=True).start()

    def send(o: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(o) + "\n")
        proc.stdin.flush()

    try:
        send({"method": "initialize", "id": 0,
              "params": {"clientInfo": {"name": "yok3x", "title": "yok3x", "version": "2.2"}}})
        deadline = time.time() + timeout
        while 0 not in responses and time.time() < deadline:
            time.sleep(0.05)
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 2, "params": {}})
        got.wait(timeout=max(0.5, deadline - time.time()))
    finally:
        _kill_tree(proc)
    res = responses.get(2) or {}
    return res.get("rateLimits") if isinstance(res, dict) else None


def _kill_tree(proc: "subprocess.Popen") -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=5)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- codex (파일 실측: stale 가능)

def _probe_codex_sessions(backend: str, conf: dict[str, Any]) -> LimitReading:
    root = Path(conf.get("sessions_dir") or (Path.home() / ".codex" / "sessions")).expanduser()
    if not root.exists():
        return LimitReading(backend, "codex_sessions", ok=False, real=True,
                            error=f"codex 세션 폴더 없음: {root}")
    try:
        files = sorted(root.rglob("rollout-*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as e:
        return LimitReading(backend, "codex_sessions", ok=False, real=True, error=str(e))
    for f in files[:12]:                 # 최신 파일들만 훑어 가장 최근 rate_limits 확보
        rl = _latest_rate_limits(f)
        if not rl:
            continue
        windows: list[Window] = []
        for key in ("primary", "secondary"):     # 이름은 window_minutes 길이로 유도(위치 아님)
            seg = rl.get(key) or {}
            up = seg.get("used_percent")
            if up is None:
                continue
            mins = _int(seg.get("window_minutes"))
            windows.append(Window(name=_window_name(mins), used_percent=float(up),
                                  resets_at=_num(seg.get("resets_at")),
                                  window_minutes=mins))
        if windows:
            plan = rl.get("plan_type") or "?"
            det = " · ".join(f"{w.name} {w.used_percent:.0f}%" for w in windows)
            return LimitReading(backend, "codex_sessions", ok=True, real=True,
                                windows=windows, detail=f"plan={plan} {det}")
    return LimitReading(backend, "codex_sessions", ok=False, real=True,
                        error="rate_limits 이벤트 미발견(대화형 codex 사용 이력 필요)")


def _latest_rate_limits(f: Path) -> dict | None:
    """rollout 파일의 마지막 rate_limits(=가장 최근 사용률)를 반환."""
    try:
        lines = f.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"rate_limits"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
        rl = payload.get("rate_limits") or d.get("rate_limits")
        if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
            return rl
    return None


# ------------------------------------------ claude (라이브 실측: OAuth usage 엔드포인트)
# codex의 app-server RPC에 대응하는 claude 실측 경로. Max/Pro 구독 OAuth 토큰으로
# GET /api/oauth/usage 를 호출하면 5h/7d used_percent + 리셋 시각을 준다(메시지 소비 0).
# 비공식·미문서 엔드포인트라 실패 시 트랜스크립트 추정 → 원장으로 명시적 열화한다.
_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_OAUTH_LIVE_CACHE: dict[str, tuple[float, LimitReading]] = {}


def _claude_oauth_token(conf: dict[str, Any]) -> tuple[str | None, str]:
    """~/.claude/.credentials.json 의 구독 OAuth 액세스 토큰. (토큰, 오류사유)."""
    p = Path(conf.get("credentials_path")
             or (Path.home() / ".claude" / ".credentials.json")).expanduser()
    if not p.exists():
        return None, f"OAuth credentials 없음: {p}"
    try:
        oauth = (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError) as e:
        return None, f"credentials 읽기 실패: {type(e).__name__}"
    tok = oauth.get("accessToken")
    if not tok:
        return None, "accessToken 없음(구독 로그인 필요)"
    exp = oauth.get("expiresAt")   # ms epoch
    if exp and float(exp) / 1000.0 < time.time():
        return None, "OAuth 토큰 만료(claude로 한 번 요청하면 자동 갱신)"
    return tok, ""


def _fetch_claude_oauth_usage(backend: str, conf: dict[str, Any]) -> LimitReading:
    token, err = _claude_oauth_token(conf)
    if not token:
        return LimitReading(backend, "claude_oauth", ok=False, real=True, error=err)
    # User-Agent(claude-code 식별)가 없으면 엔드포인트가 공격적으로 429를 낸다.
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": conf.get("oauth_beta", "oauth-2025-04-20"),
        "User-Agent": conf.get("user_agent", "claude-cli/2.1 (external, cli)"),
    }
    url = conf.get("usage_url", _CLAUDE_USAGE_URL)
    timeout = int(conf.get("timeout_sec", 15))
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        hint = ("토큰 만료/미인증" if e.code in (401, 403)
                else "호출 과다(min_interval_sec↑)" if e.code == 429 else "")
        return LimitReading(backend, "claude_oauth", ok=False, real=True,
                            error=f"HTTP {e.code} {hint}".strip())
    except Exception as e:
        return LimitReading(backend, "claude_oauth", ok=False, real=True,
                            error=f"{type(e).__name__}: {e}")
    windows: list[Window] = []
    # 5h/7d 집계 + 모델별 7d(계정이 내려줄 때만; 대부분 집계만·null). utilization은 이미 퍼센트.
    # 모델별도 windows에 넣어 guard의 '가장 빡빡한 창' 원칙에 포함시키고 mat에 함께 표시한다.
    for nm, key in (("5h", "five_hour"), ("7d", "seven_day"),
                    ("7d·opus", "seven_day_opus"), ("7d·sonnet", "seven_day_sonnet")):
        seg = data.get(key)
        if not isinstance(seg, dict) or seg.get("utilization") is None:
            continue
        windows.append(Window(nm, float(seg["utilization"]),
                              resets_at=_parse_iso(seg.get("resets_at"))))
    # 모델 스코프 주간(limits[].weekly_scoped, scope.model) — Fable/Sonnet 등 모델별 주간한도.
    # seven_day_opus/sonnet 필드는 이 계정에서 null이라, 이 배열이 실제 모델별 소스다.
    for L in (data.get("limits") or []):
        if L.get("group") != "weekly":
            continue
        model = ((L.get("scope") or {}).get("model") or {}).get("display_name")
        pct = L.get("percent")
        if model and pct is not None:
            windows.append(Window(f"7d·{model}", float(pct),
                                  resets_at=_parse_iso(L.get("resets_at"))))
    if not windows:
        return LimitReading(backend, "claude_oauth", ok=False, real=True,
                            error="usage 응답에 five_hour/seven_day 없음")
    det = " · ".join(f"{w.name} {w.used_percent:.0f}%" for w in windows)
    # 추가 크레딧: 플랜 초과분 버퍼(정보성). 활성일 때만 표시하고 guard 창엔 넣지 않는다
    # — 크레딧은 한도 '초과 후 사용'하는 버퍼라 사용률을 정지 신호로 쓰면 안 되기 때문.
    xu = data.get("extra_usage") or {}
    if xu.get("is_enabled"):
        det += (f" · 크레딧 {xu.get('used_credits', 0)}/{xu.get('monthly_limit', 0)}"
                f"{xu.get('currency', 'USD')}({float(xu.get('utilization', 0) or 0):.0f}%)")
    return LimitReading(backend, "claude_oauth", ok=True, real=True,
                        windows=windows, detail=f"{det} (live)")


def _probe_claude_oauth(backend: str, conf: dict[str, Any]) -> LimitReading:
    """라이브 실측 → (실패 시) 트랜스크립트 추정 → 원장. min_interval_sec로 호출 제한."""
    interval = float(conf.get("min_interval_sec", 60))
    hit = _OAUTH_LIVE_CACHE.get(backend)
    if hit and (time.time() - hit[0]) < interval:
        return hit[1]
    live = _fetch_claude_oauth_usage(backend, conf)
    if live.ok:
        _OAUTH_LIVE_CACHE[backend] = (time.time(), live)
        return live
    est = _probe_claude_transcripts(backend, conf)   # 추정 폴백(plan/cap 있을 때만 ok)
    if est.ok:
        est.detail += f" · live실패({live.error})"
        return est
    return live


# ---------------------------------------------------------------- claude (롤링 추정)

def _claude_root(conf: dict[str, Any]) -> Path:
    return Path(conf.get("projects_dir") or (Path.home() / ".claude" / "projects")).expanduser()


def _resolve_claude_caps(conf: dict[str, Any]) -> tuple[float, float]:
    """상한 결정: 직접 지정(limit_*_tokens) > plan 프리셋 > 0(원장 폴백)."""
    from .config import PLAN_PRESETS
    cap5 = float(conf.get("limit_5h_tokens", 0) or 0)
    cap7 = float(conf.get("limit_7d_tokens", 0) or 0)
    plan = conf.get("plan")
    if plan and (cap5 <= 0 or cap7 <= 0):
        preset = PLAN_PRESETS.get("claude", {}).get(plan)
        if preset:
            if cap5 <= 0:
                cap5 = float(preset["limit_5h_tokens"])
            if cap7 <= 0:
                cap7 = float(preset["limit_7d_tokens"])
    return cap5, cap7


def claude_rolling_tokens(conf: dict[str, Any], window: str) -> int:
    """calibrate용: 지정 창(5h/7d)의 현재 롤링 토큰 합계."""
    secs = 5 * 3600 if window == "5h" else 7 * 24 * 3600
    return _rolling_claude_tokens(_claude_root(conf), time.time(), secs)


def _probe_claude_transcripts(backend: str, conf: dict[str, Any]) -> LimitReading:
    root = _claude_root(conf)
    if not root.exists():
        return LimitReading(backend, "claude_transcripts", ok=False, real=False,
                            error=f"claude transcript 폴더 없음: {root}")
    cap5, cap7 = _resolve_claude_caps(conf)
    now = time.time()
    tok5 = _rolling_claude_tokens(root, now, 5 * 3600)
    tok7 = _rolling_claude_tokens(root, now, 7 * 24 * 3600)
    windows: list[Window] = []
    if cap5 > 0:
        windows.append(Window("5h", 100.0 * tok5 / cap5, window_minutes=300))
    if cap7 > 0:
        windows.append(Window("7d", 100.0 * tok7 / cap7, window_minutes=10080))
    detail = (f"5h {tok5:,}tok" + (f"/{int(cap5):,}" if cap5 else "")
              + f", 7d {tok7:,}tok" + (f"/{int(cap7):,}" if cap7 else ""))
    if not windows:
        return LimitReading(backend, "claude_transcripts", ok=False, real=False,
                            detail=detail + " (cap 미설정)",
                            error="limits.claude.limit_5h_tokens/limit_7d_tokens 미설정")
    return LimitReading(backend, "claude_transcripts", ok=True, real=False,
                        windows=windows, detail=detail)


def _rolling_claude_tokens(root: Path, now: float, window_sec: float) -> int:
    cutoff = now - window_sec
    total = 0
    try:
        files = list(root.rglob("*.jsonl"))
    except OSError:
        return 0
    for f in files:
        try:
            if f.stat().st_mtime < cutoff - 3600:   # 창보다 오래 전에 끝난 파일은 스킵
                continue
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(d.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            u = msg.get("usage") or d.get("usage") or {}
            if not isinstance(u, dict):
                continue
            total += (_int(u.get("input_tokens")) + _int(u.get("output_tokens"))
                      + _int(u.get("cache_creation_input_tokens"))
                      + _int(u.get("cache_read_input_tokens")))
    return total


# ---------------------------------------------------------------- command (범용: ccusage/tokscale/CodexBar export)

def _probe_command(backend: str, conf: dict[str, Any]) -> LimitReading:
    cmd = conf.get("command")
    if not cmd:
        return LimitReading(backend, "command", ok=False, real=False,
                            error="limits.<backend>.command 미설정")
    cmd = [str(a) for a in cmd]
    resolved = shutil.which(cmd[0])        # 윈도우: npx→npx.cmd 등 .cmd 심 해석
    if resolved:
        cmd[0] = resolved
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=int(conf.get("timeout_sec", 60)))
    except FileNotFoundError:
        return LimitReading(backend, "command", ok=False, real=False,
                            error=f"실행 파일 없음: {cmd[0]!r} (cmd: {shlex.join(cmd)})")
    except subprocess.TimeoutExpired:
        return LimitReading(backend, "command", ok=False, real=False,
                            error=f"probe timeout: {shlex.join(cmd)}")
    data = _first_json(proc.stdout)
    if data is None:
        return LimitReading(backend, "command", ok=False, real=False,
                            error=f"probe JSON 파싱 실패(exit {proc.returncode}): "
                                  f"{proc.stderr.strip()[:200]}")
    windows = _windows_from_conf(data, conf)
    if not windows:
        return LimitReading(backend, "command", ok=False, real=False,
                            error="probe 응답에서 사용률(windows/used_percent) 미발견")
    real = bool(conf.get("real", True))
    det = "; ".join(f"{w.name} {w.used_percent:.0f}%" for w in windows)
    return LimitReading(backend, "command", ok=True, real=real, windows=windows, detail=det)


def _windows_from_conf(data: Any, conf: dict[str, Any]) -> list[Window]:
    """설정된 매핑으로 외부 JSON에서 윈도우를 뽑는다.

    A) limits.<b>.windows = [{"name":"5h","percent_path":"a.b.used_percent",
                              "resets_at_path":"a.b.resets_at"}, ...]  (범용 dotted-path)
    B) limits.<b>.parse = "ccusage_active" + limit_5h_usd  (ccusage blocks --active --json)
    C) 매핑 미지정: 응답에서 재귀적으로 첫 used_percent 를 찾아 단일 윈도우.
    """
    win_specs = conf.get("windows")
    if isinstance(win_specs, list) and win_specs:
        out: list[Window] = []
        for spec in win_specs:
            pct = _num(_dig(data, spec.get("percent_path", "")))
            if pct is None:
                continue
            out.append(Window(name=str(spec.get("name", "win")),
                              used_percent=float(pct),
                              resets_at=_num(_dig(data, spec.get("resets_at_path", "")))))
        return out
    if conf.get("parse") == "ccusage_active":
        blocks = data.get("blocks") if isinstance(data, dict) else None
        cap = float(conf.get("limit_5h_usd", 0) or 0)
        if isinstance(blocks, list) and cap > 0:
            for b in blocks:
                if b.get("isActive"):
                    cost = float(b.get("costUSD", 0) or 0)
                    end = _parse_iso(b.get("endTime"))
                    return [Window("5h", 100.0 * cost / cap, resets_at=end, window_minutes=300)]
        return []
    found = _find_used_percent(data)
    return [Window("limit", float(found))] if found is not None else []


# ---------------------------------------------------------------- helpers

def _dig(obj: Any, path: str) -> Any:
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            try:
                cur = cur[int(part)]
            except IndexError:
                return None
        else:
            return None
    return cur


def _find_used_percent(obj: Any, depth: int = 0) -> float | None:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        if "used_percent" in obj and _num(obj["used_percent"]) is not None:
            return float(obj["used_percent"])
        for v in obj.values():
            r = _find_used_percent(v, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_used_percent(v, depth + 1)
            if r is not None:
                return r
    return None


def _first_json(text: str) -> Any:
    """stdout 앞 노이즈('Loaded cached credentials.' 등)를 건너뛰고 첫 JSON 값 파싱."""
    for opener in ("{", "["):
        i = text.find(opener)
        if i == -1:
            continue
        try:
            return json.loads(text[i:])
        except json.JSONDecodeError:
            # 뒤에 로그가 더 붙은 경우: raw_decode 로 앞부분만
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
    return None


def _parse_iso(s: Any) -> float | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
