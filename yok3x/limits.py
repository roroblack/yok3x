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
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config


logger = logging.getLogger(__name__)


@dataclass
class Window:
    name: str                       # "5h" | "7d" | 등
    used_percent: float             # 0~100 (100 초과 가능)
    resets_at: float | None = None  # epoch seconds(절대 시각) — codex는 실제 리셋 시각
    window_minutes: int | None = None
    # 라이브 API가 토큰 수를 주지 않으면 None. 0(측정됐지만 사용 없음)과 미측정을 구분한다.
    used_tokens: int | None = None
    limit_tokens: int | None = None

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


# R-4: provenance를 **enum**으로 승격한다. detail 문자열에 '(추정)' 같은 라벨을 섞어두면
# 자동화가 사람용 문구를 파싱해야 해서 취약하다(리포트 10). source(원천 채널)는 그대로 두고,
# '이 숫자를 얼마나 믿을 수 있나'를 별도 축으로 노출한다.
PROVENANCE_MEASURED = "measured"        # 공급자가 보고한 실측(oauth·app-server·statusline 등)
PROVENANCE_ESTIMATED = "estimated"      # 로컬 추정(트랜스크립트 롤링 등) — 오차 있음
PROVENANCE_LEDGER = "ledger"            # 실측 없음, 자체 예산 원장(로컬 자정 리셋)
PROVENANCE_UNAVAILABLE = "unavailable"  # 사용률을 못 구함(비활성·실패)
PROVENANCE_VALUES = (PROVENANCE_MEASURED, PROVENANCE_ESTIMATED,
                     PROVENANCE_LEDGER, PROVENANCE_UNAVAILABLE)


@dataclass
class LimitReading:
    backend: str
    source: str                     # codex_sessions | claude_transcripts | command | ledger | disabled | none
    ok: bool                        # 사용률을 신뢰성 있게 얻었는가
    real: bool                      # 서버 보고 실측이면 True, 롤링 추정이면 False
    windows: list[Window] = field(default_factory=list)
    detail: str = ""
    error: str = ""

    def provenance(self) -> str:
        """R-4: 신뢰 등급 enum. 문자열 detail 파싱 없이 자동화가 소비한다."""
        if not self.ok:
            return PROVENANCE_UNAVAILABLE
        if self.source == "ledger":
            return PROVENANCE_LEDGER
        return PROVENANCE_MEASURED if self.real else PROVENANCE_ESTIMATED

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
    if backend == "gemini":
        # 진짜 동적 소스: Google Generative Language API /v1beta/models(=gemini SDK/CLI가 쓰는 그 목록).
        # 키는 config 주도로 해석(어느 env·파일에서 읽을지 설정이 지정) → 평문 하드코딩 아님.
        conf = (cfg.yok3x.get("limits") or {}).get("gemini") or {}
        key = _gemini_api_key(conf)
        if key:
            # 1순위: 실시간 Google API(키 있을 때 가장 정확)
            base = conf.get("models_url",
                            "https://generativelanguage.googleapis.com/v1beta/models")
            url = f"{base}?key={urllib.parse.quote(key)}&pageSize=1000"
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            out = []
            for m in data.get("models", []):
                name = (m.get("name") or "").split("/")[-1]   # "models/gemini-2.5-pro" → 슬러그
                methods = m.get("supportedGenerationMethods") or []
                if name and (not methods or "generateContent" in methods):
                    out.append(name)
            return out
        # 2순위: 키 없음(이 계정은 Antigravity/CloudSDK 암호화 OAuth라 키 접근 불가). gemini CLI
        # 번들의 GEMINI_MODELS 레지스트리를 읽는다 — 설치된 CLI 버전이 지원하는 실제 모델 집합
        # (codex의 models_cache.json과 동급의 실제 소스, CLI 업데이트 시 갱신).
        return _gemini_bundle_models()
    if backend == "local":
        # 로컬 OpenAI 호환 서버의 /v1/models(설치·기동된 모델). 서버 없으면 빈 목록.
        conf = (cfg.yok3x.get("limits") or {}).get("local") or {}
        base = str((cfg.backends.get("local") or {}).get("base_url")
                   or conf.get("base_url") or "http://localhost:8000/v1").rstrip("/")
        try:
            with urllib.request.urlopen(base + "/models", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return []
        return [m.get("id") for m in data.get("data", []) if m.get("id")]
    return []


def _gemini_api_key(conf: dict[str, Any]) -> str:
    """gemini API 키 해석. 우선순위: conf['api_key'] → conf['api_key_path'] 파일 → 환경변수 →
    **gemini CLI와 동일한 .env 탐색**(loadEnvironment/findEnvFile 미러). 없으면 ''(명시적 폴백).

    gemini CLI는 키를 env에 직접 두는 대신 .env로 로드하는 경우가 많아, 같은 규칙으로 찾아야
    yok3x도 gemini가 인증되는 바로 그 위치에서 키를 얻는다. 키 값은 반환만 하고 노출하지 않는다."""
    names = conf.get("api_key_env") or ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"]
    if isinstance(names, str):
        names = [names]
    k = (conf.get("api_key") or "").strip()
    if k:
        return k
    p = conf.get("api_key_path")
    if p:
        fp = Path(p).expanduser()
        if fp.exists():
            return fp.read_text(encoding="utf-8").strip()
    for n in names:                       # 이미 프로세스 환경에 있으면 그대로
        v = os.environ.get(n)
        if v:
            return v.strip()
    # gemini의 findEnvFile 미러: cwd→상위로 각 단계 .gemini/.env 다음 .env, 그다음 홈.
    try:
        start = Path(conf.get("env_search_from") or Path.cwd()).resolve()
    except Exception:
        start = Path.home()
    seen, candidates = set(), []
    d = start
    while True:
        candidates += [d / ".gemini" / ".env", d / ".env"]
        if d.parent == d:
            break
        d = d.parent
    candidates += [Path.home() / ".gemini" / ".env", Path.home() / ".env"]
    for env_file in candidates:
        if env_file in seen:
            continue
        seen.add(env_file)
        v = _read_env_key(env_file, names)
        if v:
            return v
    return ""


def _gemini_bundle_dir() -> Path | None:
    """설치된 gemini CLI의 bundle 디렉터리를 찾는다(shim 위치 + npm 전역 경로 후보)."""
    cands: list[Path] = []
    exe = shutil.which("gemini")
    if exe:
        p = Path(exe).resolve().parent
        cands += [p / "node_modules" / "@google" / "gemini-cli" / "bundle",
                  p.parent / "lib" / "node_modules" / "@google" / "gemini-cli" / "bundle"]
    cands += [Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "@google" / "gemini-cli" / "bundle",
              Path("/usr/local/lib/node_modules/@google/gemini-cli/bundle"),
              Path("/usr/lib/node_modules/@google/gemini-cli/bundle")]
    for c in cands:
        if c.is_dir():
            return c
    return None


def _gemini_bundle_models() -> list[str]:
    """gemini CLI 번들의 GEMINI_MODELS Set(모델 검증용 정식 레지스트리)을 읽어 슬러그 목록 반환.
    Set 멤버는 변수 참조라 `var NAME = "gemini-.."` 할당을 해석한다. 못 찾으면 []."""
    bundle = _gemini_bundle_dir()
    if not bundle:
        return []
    members: list[str] = []
    assigns: dict[str, str] = {}
    for f in bundle.glob("*.js"):
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        i = t.find("GEMINI_MODELS = ")
        if i >= 0:
            j = t.find("]", i)
            if j >= 0:
                members = re.findall(r"\b([A-Z][A-Z0-9_]+)\b", t[i:j + 1])
        for m in re.finditer(r'\b([A-Z][A-Z0-9_]+)\s*=\s*"(gemini[^"]*|gemma[^"]*)"', t):
            assigns.setdefault(m.group(1), m.group(2))
    return [assigns[m] for m in members if m in assigns]


def _read_env_key(env_file: Path, names: list[str]) -> str:
    """.env 파일에서 names 중 첫 키의 값을 읽는다(KEY=VALUE, 따옴표/export 처리). 실패 시 ''."""
    try:
        if not env_file.is_file():
            return ""
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            if key in names:
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


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
            return _probe_claude_oauth(backend, conf, cfg)
        if typ == "claude_transcripts":
            return _probe_claude_transcripts(backend, conf)
        if typ == "claude_statusline":
            return _probe_claude_statusline(backend, conf)
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


def codex_percent_at(conf: dict[str, Any], at_ts: float,
                     window_start: float | None = None,
                     max_files: int = 40) -> float | None:
    """F2-10: **`at_ts` 시점의 codex 주간 사용률(%)** 을 세션 로그의 rate_limits 시계열에서 찾는다.

    codex는 claude와 달리 토큰 트랜스크립트가 없어 '오늘 얼마 썼나'를 못 냈다(현재값 스냅샷만).
    그런데 rollout 로그의 `token_count` 이벤트에 **timestamp + used_percent**가 함께 남는다 —
    즉 **주간 %의 시계열**이 이미 디스크에 있다. `at_ts` 이하의 마지막 관측을 그 시점의 사용률로
    본다(계단 함수). 관측이 하나도 없으면(로그 없음·그 이전 데이터 없음) None → 호출자가 폴백.

    반환값은 '그 시점까지 누적된 주간 %'다. 따라서 `현재% − codex_percent_at(하루시작)`이
    **오늘 소비**가 된다. 프로세스 재기동에 불변(디스크 로그 기반)이라 스냅샷 모델의 약점(BUG-37)이 없다.

    `window_start`(현재 주간 창 시작)를 주면 **그 이전 관측은 무시**한다 — 시계열은 주간 리셋을
    가로질러 이어지므로, 리셋 직전의 높은 %(이전 창의 누적)를 새 창의 기준선으로 쓰면 안 된다
    (실측: 리셋 당일 하루시작 기준선이 80%로 잡혀 오늘 소비가 0으로 뭉개졌다).
    창 안에 관측이 없으면 **0.0**을 돌려준다 — 창이 막 시작해 아직 사용이 없다는 뜻이다.
    """
    # 창 시작 시점(또는 그 이전)의 누적은 **정의상 0%** — 파일을 한 개도 읽을 필요가 없다.
    # 리셋 당일에는 day_start == window_start라 이 경로가 늘 타는데, 없으면 '관측 없음'을 확인하려고
    # 창 안 파일을 전부 훑게 된다(실측: 수십 MB). 캐시가 아니라 **불필요한 일을 안 하는** 것이다.
    if window_start is not None and at_ts <= window_start:
        return 0.0
    root = Path(conf.get("sessions_dir") or (Path.home() / ".codex" / "sessions")).expanduser()
    if not root.exists():
        return None
    try:                      # 최근 파일만 본다(오래된 세션은 이번 주 창과 무관)
        files = sorted(root.rglob("rollout-*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    except OSError:
        return None
    best_ts, best_pct = None, None
    for f in files:                       # mtime 내림차순(최신 우선)
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime < at_ts - 8 * 86400:                      # 창보다 오래된 파일은 스킵
            continue
        # **증명 가능한 조기 종료**(캐시 아님): 파일 안의 이벤트 시각은 그 파일의 마지막 쓰기(mtime)보다
        # 늦을 수 없다. 따라서 mtime이 이미 찾은 최선값보다 이르면, 그 파일도 그보다 오래된 나머지
        # 파일들도 최선을 갱신할 수 없다 → 읽지 않고 멈춘다. 정확도 손실 0, 읽는 파일 수만 준다.
        if best_ts is not None and mtime <= best_ts:
            break
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"rate_limits"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(d.get("timestamp"))
            if ts is None or ts > at_ts:
                continue
            if window_start is not None and ts < window_start:
                continue                      # 이전 주간 창의 누적 — 새 창의 기준선이 될 수 없다
            payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
            rl = payload.get("rate_limits") or d.get("rate_limits")
            if not isinstance(rl, dict):
                continue
            seg = rl.get("primary") or {}
            pct = _num(seg.get("used_percent"))
            if pct is None:
                continue
            if best_ts is None or ts > best_ts:
                best_ts, best_pct = ts, float(pct)
    if best_pct is None and window_start is not None and at_ts >= window_start:
        return 0.0                            # 창은 시작됐고 그 안에 사용 기록이 없다 = 0%
    return best_pct


# ------------------------------------------ claude (라이브 실측: OAuth usage 엔드포인트)
# codex의 app-server RPC에 대응하는 claude 실측 경로. Max/Pro 구독 OAuth 토큰으로
# GET /api/oauth/usage 를 호출하면 5h/7d used_percent + 리셋 시각을 준다(메시지 소비 0).
# 비공식·미문서 엔드포인트라 실패 시 트랜스크립트 추정 → 원장으로 명시적 열화한다.
_CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_OAUTH_LIVE_CACHE: dict[str, tuple[float, LimitReading]] = {}
# 실패 백오프: {backend: (다음_허용_epoch, 연속실패수)}. 429는 지수 백오프(버그성 rate-limit을
# 두들기지 않게), 401/403은 장기 중단(토큰 무효 — 재시도 무의미, Claude Code가 갱신하면 회복).
_OAUTH_BACKOFF: dict[str, tuple[float, int]] = {}


def _oauth_live_path(cfg: "Config | None") -> "Path | None":
    """마지막 성공 실측을 디스크에 남길 경로(.yok3x/oauth_live.json). cfg 없으면 None."""
    if cfg is None:
        return None
    try:
        return cfg.paths.yok3x_dir / "oauth_live.json"
    except Exception:
        return None


def _save_oauth_live(cfg: "Config | None", backend: str, ts: float, reading: LimitReading) -> None:
    """성공 실측을 디스크에 영속화. _OAUTH_LIVE_CACHE는 프로세스별 인메모리라 CLI 호출·GUI
    재기동마다 사라져 매 프로세스가 OAuth 재시도→429→트랜스크립트로 떨어지며 '오늘 소비'가
    0↔실측으로 깜빡였다(사용자 지적). 디스크에 남기면 stale-while-error가 프로세스 경계를 넘어
    유지되고 CLI/GUI가 같은 실측을 공유한다. 원자적 쓰기(torn write 방지, BUG-32와 동일 이유)."""
    p = _oauth_live_path(cfg)
    if p is None:
        return
    payload = {
        "backend": backend,
        "at": ts,
        "source": reading.source,
        "detail": reading.detail,
        "windows": [
            {"name": w.name, "used_percent": w.used_percent, "resets_at": w.resets_at,
             "window_minutes": w.window_minutes, "used_tokens": w.used_tokens,
             "limit_tokens": w.limit_tokens}
            for w in reading.windows
        ],
    }
    try:
        store = {}
        if p.exists():
            try:
                store = json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                store = {}
        store[backend] = payload
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except Exception:
        logger.exception("OAuth 실측 디스크 저장 실패(라이브 실측은 유지)")


def _load_oauth_live(cfg: "Config | None", backend: str) -> "tuple[float, LimitReading] | None":
    """디스크에 영속화된 마지막 성공 실측을 (ts, LimitReading)으로 복원. 없거나 손상 시 None.
    인메모리 캐시가 빈 새 프로세스가 이전 실측을 이어받게 해 소스 플립을 막는다."""
    p = _oauth_live_path(cfg)
    if p is None or not p.exists():
        return None
    try:
        store = json.loads(p.read_text(encoding="utf-8")) or {}
        rec = store.get(backend)
        if not rec:
            return None
        wins = [Window(name=w.get("name", ""), used_percent=float(w.get("used_percent", 0.0)),
                       resets_at=w.get("resets_at"), window_minutes=w.get("window_minutes"),
                       used_tokens=w.get("used_tokens"), limit_tokens=w.get("limit_tokens"))
                for w in rec.get("windows", [])]
        reading = LimitReading(backend, rec.get("source", "claude_oauth"), ok=True, real=True,
                               windows=wins, detail=rec.get("detail", ""))
        return float(rec.get("at", 0.0)), reading
    except Exception:
        logger.exception("OAuth 실측 디스크 복원 실패")
        return None


def _save_weekly_phase(cfg: "Config | None", windows: list) -> None:
    """실측(oauth/statusline) 성공 시 7d 리셋 시각을 config.limits.claude.weekly_reset_epoch에 저장한다.
    이후 트랜스크립트 폴백이 이 주간 위상을 전개해 7d 리셋 카운트다운을 보여준다(실측 유래, 지어내지 않음)."""
    if cfg is None:
        return
    for w in windows or []:
        if getattr(w, "name", None) == "7d" and getattr(w, "resets_at", None):
            cl = cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})
            if cl.get("weekly_reset_epoch") != float(w.resets_at):
                cl["weekly_reset_epoch"] = float(w.resets_at)
                cfg.save_yok3x()
            return


# 토큰 자체 갱신 상태(경로별): 백오프·회로차단용. 데이터를 지어내지 않는다 — 실패 시 폴백.
_REFRESH_STATE: dict[str, dict] = {}

# 설정 파일별 마지막 자동 보정 저장 시각. 라이브 조회가 자주 성공해도 yok3x.json을 계속
# 덮어쓰지 않도록 실제 저장에 성공했을 때만 갱신한다(_REFRESH_STATE와 같은 모듈 상태 패턴).
_CLAUDE_CALIBRATION_STATE: dict[str, float] = {}
_CLAUDE_CALIBRATION_LOCK = threading.Lock()


def _read_oauth(p: Path) -> dict:
    try:
        return (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_oauth_atomic(p: Path, updates: dict) -> bool:
    """credentials.json의 claudeAiOauth를 원자적으로 갱신(재로드 병합 + 기존 백업). 성공 True."""
    try:
        full = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
        if not isinstance(full, dict):
            full = {}
        try:                                    # 복구용 백업(회전 직후 크래시 대비)
            p.with_suffix(".json.bak").write_text(
                json.dumps(full, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        oauth = full.get("claudeAiOauth") or {}
        oauth.update(updates)
        full["claudeAiOauth"] = oauth
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)                          # 원자적 교체
        return True
    except OSError:
        return False


def _refresh_claude_token(conf: dict[str, Any], p: Path) -> str | None:
    """near-expiry면 refresh_token으로 access_token 갱신. 성공 시 새 토큰, 실패/불가면 None(폴백).
    4xx 영구오류는 회로차단, 429/5xx/네트워크만 백오프 재시도. Claude Code와 파일 공유 → 재로드 병합."""
    st = _REFRESH_STATE.setdefault(str(p), {"last": 0.0, "fails": 0, "disabled": ""})
    if st["disabled"]:
        return None
    now = time.time()
    min_interval = float(conf.get("min_refresh_interval_sec", 60))
    wait = min_interval * (2 ** min(st["fails"], 5)) if st["fails"] else min_interval
    if now - st["last"] < wait:                 # 폭주 방지 + 지수 백오프
        return None
    oauth = _read_oauth(p)                       # 재로드 — 다른 클라이언트가 이미 갱신했을 수 있음
    exp = oauth.get("expiresAt")
    margin = float(conf.get("refresh_margin_sec", 300))
    if exp and float(exp) / 1000.0 - now > margin:
        return oauth.get("accessToken")         # 이미 충분히 유효(Claude Code가 갱신함) → 그대로
    rt = oauth.get("refreshToken")
    client_id = (conf.get("client_id") or "").strip()
    token_url = (conf.get("token_url") or "").strip()
    if not rt or not client_id or not token_url:
        st["disabled"] = "refresh 설정 없음(refreshToken/client_id/token_url) — 추측 안 함"
        return None
    st["last"] = now
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": rt,
                       "client_id": client_id}).encode("utf-8")
    # User-Agent 없으면 엔드포인트 WAF가 403을 낸다(usage 프로브와 동일). anthropic-beta도 맞춘다.
    req = urllib.request.Request(token_url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": conf.get("user_agent", "claude-cli/2.1 (external, cli)"),
        "anthropic-beta": conf.get("oauth_beta", "oauth-2025-04-20"),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500 and e.code != 429:   # invalid_grant 등 영구오류 → 회로차단(재인증 필요)
            st["disabled"] = f"refresh 4xx({e.code}) — `claude` 재로그인 필요"
        else:
            st["fails"] += 1                        # 429/5xx → 백오프 재시도
        return None
    except Exception:
        st["fails"] += 1
        return None
    new_at = data.get("access_token")
    if not new_at:
        st["fails"] += 1
        return None
    updates = {
        "accessToken": new_at,
        "refreshToken": data.get("refresh_token") or rt,   # 회전 시 새 값, 아니면 유지
        "expiresAt": int((now + int(data.get("expires_in", 3600))) * 1000),
    }
    # 서버가 리프레시 토큰 수명을 주면 반영한다. 안 주면 기존 값을 그대로 둔다 — 추측해서 쓰면
    # 상태 배지가 '재로그인 필요' 오경보를 낸다(§5.5: 근거 없는 값 만들지 않음). 갱신 자체는
    # 이 값과 무관하게 동작하므로(저장된 refreshToken만 쓰면 됨) 이건 표시 정확도 문제다.
    rt_exp_in = data.get("refresh_token_expires_in")
    if rt_exp_in:
        try:
            updates["refreshTokenExpiresAt"] = int((now + int(rt_exp_in)) * 1000)
        except (TypeError, ValueError):
            pass
    ok = _write_oauth_atomic(p, updates)
    if not ok:
        st["fails"] += 1
        return None
    st["fails"] = 0
    return new_at


def claude_token_status(conf: dict[str, Any]) -> dict[str, Any]:
    """claude OAuth 토큰 상태를 **읽기만** 해서 반환(GUI 표시용).

    절대 갱신을 트리거하지 않는다 — build_state가 주기적으로 부르므로 여기서 refresh를 걸면
    리프레시 토큰이 계속 회전한다. 반환: exists·expired·mins_left·refresh_ok.
    """
    p = Path(conf.get("credentials_path")
             or (Path.home() / ".claude" / ".credentials.json")).expanduser()
    out: dict[str, Any] = {"exists": False, "expired": None, "mins_left": None, "refresh_ok": None}
    if not p.exists():
        return out
    try:
        oauth = (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError):
        return out
    out["exists"] = bool(oauth.get("accessToken"))
    exp, rexp = oauth.get("expiresAt"), oauth.get("refreshTokenExpiresAt")
    now = time.time()
    if exp:
        left = float(exp) / 1000.0 - now
        out["expired"] = left <= 0
        out["mins_left"] = int(left // 60)
    if rexp:                       # 리프레시 토큰이 살아 있으면 자동갱신으로 복구 가능
        out["refresh_ok"] = (float(rexp) / 1000.0 - now) > 0
    return out


def _claude_oauth_token(conf: dict[str, Any]) -> tuple[str | None, str]:
    """~/.claude/.credentials.json 의 구독 OAuth 액세스 토큰. (토큰, 오류사유).
    auto_refresh on이고 만료 임박이면 refresh_token으로 자체 갱신 시도(실패=기존 동작 폴백)."""
    p = Path(conf.get("credentials_path")
             or (Path.home() / ".claude" / ".credentials.json")).expanduser()
    if not p.exists():
        return None, f"OAuth credentials 없음: {p}"
    try:
        oauth = (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError) as e:
        return None, f"credentials 읽기 실패: {type(e).__name__}"
    exp = oauth.get("expiresAt")   # ms epoch
    near = bool(exp) and float(exp) / 1000.0 - time.time() < float(conf.get("refresh_margin_sec", 300))
    if conf.get("auto_refresh") and near:
        new = _refresh_claude_token(conf, p)
        if new:
            return new, ""
    tok = oauth.get("accessToken")
    if not tok:
        return None, "accessToken 없음(구독 로그인 필요)"
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


def _probe_claude_oauth(backend: str, conf: dict[str, Any],
                        cfg: Config | None = None) -> LimitReading:
    """라이브 실측 → (실패 시) 최근 실측 stale 유지 → 추정 → 원장. min_interval_sec로 호출 제한."""
    interval = float(conf.get("min_interval_sec", 60))
    max_stale = float(conf.get("max_stale_sec", 900))   # 실측 실패 시 마지막 실측을 이만큼 유지
    now = time.time()
    hit = _OAUTH_LIVE_CACHE.get(backend)
    if hit is None:
        # 인메모리 캐시가 빈 새 프로세스(CLI 호출·GUI 재기동): 디스크에 영속화된 마지막 실측을
        # 이어받아 stale-while-error가 프로세스 경계를 넘게 한다(0↔실측 플립 방지, 사용자 지적).
        disk = _load_oauth_live(cfg, backend)
        if disk is not None:
            _OAUTH_LIVE_CACHE[backend] = disk
            hit = disk
    if hit and (now - hit[0]) < interval:
        return hit[1]
    # 실패 백오프: 아직 백오프 창이면 실제 호출을 건너뛴다(버그성 429 엔드포인트를 두들기지 않음).
    bo_until, bo_fails = _OAUTH_BACKOFF.get(backend, (0.0, 0))
    called = now >= bo_until
    if called:
        live = _fetch_claude_oauth_usage(backend, conf)
    else:
        live = LimitReading(backend, "claude_oauth", ok=False, real=True,
                            error=f"백오프 중({int(bo_until - now)}s 남음)")
    if live.ok:
        _OAUTH_LIVE_CACHE[backend] = (now, live)
        _OAUTH_BACKOFF.pop(backend, None)               # 성공 → 백오프 해제
        _save_oauth_live(cfg, backend, now, live)       # 디스크 영속화(프로세스 경계 넘어 유지)
        # 실측 반환이 주 기능이다. 로컬 transcript 읽기나 설정 저장이 실패해도 정상 live를
        # 버리면 안 되므로 보정 부작용은 완전히 격리한다. 추가 네트워크 호출은 없다.
        if cfg is not None:
            try:
                _save_weekly_phase(cfg, live.windows)   # 7d 리셋 위상 갱신(폴백서 재사용)
            except Exception:
                logger.exception("claude 주간 위상 저장 실패(라이브 실측은 유지)")
            try:
                autocalibrate_claude(cfg, conf, live)
            except Exception:
                logger.exception("claude 자동 캘리브레이션 실패(라이브 실측은 유지)")
        return live
    if called:                                          # 방금 실제 호출해 실패 → 백오프 갱신
        err = live.error or ""
        fails = bo_fails + 1
        if "401" in err or "403" in err:                # 토큰 무효 → 장기 중단(재시도 무의미)
            delay = float(conf.get("oauth_auth_fail_backoff_sec", 3600))
        else:                                           # 429/네트워크 → 지수(최대 30분)
            delay = min(1800.0, interval * (2 ** min(fails, 5)))
        _OAUTH_BACKOFF[backend] = (now + delay, fails)
    # 실측 실패(429/토큰만료 등): 원장으로 떨어뜨려 배지가 실측↔원장으로 깜빡이는 대신, 최근
    # 실측값을 유지하고 detail에 '⚠N분 전 실측'을 붙여 정직하게 표시(stale-while-error).
    # 캐시엔 성공한 실측만 저장되므로 hit[1]은 항상 진짜 실측. max_stale 지나면 아래 폴백.
    if hit and (now - hit[0]) < max_stale:
        prev = hit[1]
        age = max(1, int((now - hit[0]) / 60))
        base = prev.detail.replace(" (live)", "")
        why = live.error.split("(")[0].strip() or "실측 일시 실패"
        return replace(prev, detail=f"{base} (⚠{age}분 전 실측·{why})")
    est = _probe_claude_transcripts(backend, conf)   # 추정 폴백(plan/cap 있을 때만 ok)
    # 미보정 추정은 캐시read까지 세어 과대(수백~수천%)해질 수 있다. live 실패 시 그런 값을
    # 그대로 표시하면 "1003%" 같은 오표시가 난다 → 비현실적으로 높으면(>200%) 신뢰 불가로
    # 보고 반환하지 않는다. 그러면 check_backend가 원장(sane) 폴백으로 넘어간다(§5.5: 조용한
    # 폴백 대신, 추정이 못 미더우면 확실한 원장을 쓴다).
    if est.ok and est.ratio() <= 2.0:
        est.detail += f" · live실패({live.error})"
        return est
    if est.ok:   # 추정이 나왔으나 비현실적 — 사유를 남기고 원장 폴백에 맡김
        live.error = f"{live.error}; 추정 {est.ratio() * 100:.0f}%(미보정) 무시"
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


def autocalibrate_claude(cfg: Config, conf: dict[str, Any],
                         reading: LimitReading) -> dict[str, int | str | None]:
    """Claude live 사용률로 transcript 추정 상한을 역산해 저장한다.

    transcript 집계에는 cache read 토큰도 들어가므로 공개 플랜 프리셋만으로는 크게 과대 추정될
    수 있다. 같은 시점의 live %(지상진실)와 로컬 롤링 합계를 맞춰 집계 단위 자체를 보정한다.
    정확히 5h/7d 집계 창만 대상으로 하며 ``7d·Fable`` 같은 모델별 창은 의도적으로 제외한다.
    """
    out: dict[str, int | str | None] = {"5h": None, "7d": None, "skipped": ""}
    if not conf.get("autocalibrate", True):
        out["skipped"] = "비활성(autocalibrate=false)"
        return out

    live_by_name = {w.name: w for w in reading.windows if w.name in ("5h", "7d")}
    if not live_by_name:
        out["skipped"] = "대상 창 없음(5h/7d만 지원)"
        return out

    now = time.time()
    state_key = str(cfg.paths.yok3x_json)
    min_interval = float(conf.get("min_calib_interval_sec", 600))
    min_pct = float(conf.get("min_calib_pct", 1.0))
    reasons: list[str] = []

    with _CLAUDE_CALIBRATION_LOCK:
        last = _CLAUDE_CALIBRATION_STATE.get(state_key)
        if last is not None and now - last < min_interval:
            out["skipped"] = "rate-limit"
            return out

        # 직접 지정이 없으면 plan 프리셋을 비교 기준으로 쓴다. 새 값이 이 기준의 1/100 미만
        # 또는 100배 초과면 잘못된 작은 %·불완전 transcript일 가능성이 높아 저장하지 않는다.
        cap5, cap7 = _resolve_claude_caps(conf)
        baselines = {"5h": cap5, "7d": cap7}
        updates: dict[str, int] = {}
        keys = {"5h": "limit_5h_tokens", "7d": "limit_7d_tokens"}
        for name in ("5h", "7d"):
            win = live_by_name.get(name)
            if win is None:
                continue
            live_pct = float(win.used_percent)
            if live_pct <= 0 or live_pct < min_pct:
                reasons.append(f"{name}:live_pct<{min_pct:g}")
                continue
            toks = claude_rolling_tokens(conf, name)
            if toks <= 0:
                reasons.append(f"{name}:tokens<=0")
                continue
            cap = int(toks / (live_pct / 100.0))
            if cap <= 0:
                reasons.append(f"{name}:cap<=0")
                continue
            baseline = baselines[name]
            if baseline > 0:
                multiple = cap / baseline
                lo = float(conf.get("min_calib_multiple", 0.001))
                hi = float(conf.get("max_calib_multiple", 1000.0))
                if multiple < lo or multiple > hi:
                    reason = f"{name}:비현실 캡({multiple:.3g}x)"
                    reasons.append(reason)
                    logger.warning("claude 자동 캘리브레이션 무시: %s", reason)
                    continue
                # 회당 변화량 제한(±max_step): 5h처럼 창이 작아 %가 빠르게 변하는 곳에서 파생 cap이
                # 3배씩 널뛰던 문제(사용자 지적: 246M→793M)를 막는다. 여러 캘리브레이션에 걸쳐 참값으로
                # 수렴하되 단발 노이즈(swing)는 감쇠 — 끄지 않고 안정화(사용자 요청: 고쳐서 정확하게).
                # 이미 보정된 값(저장 override>0)에만 적용: 첫 보정은 preset에서 현실로 즉시 스냅하고,
                # 이후 보정만 클램프해 스윙을 막는다(빠른 초기 수렴 + 지속 안정성 둘 다).
                stored = float((cfg.yok3x.get("limits", {}).get("claude", {}) or {}).get(keys[name], 0) or 0)
                if stored > 0:
                    max_step = float(conf.get("calib_max_step", 0.25))
                    cap = int(max(stored * (1.0 - max_step), min(stored * (1.0 + max_step), cap)))
                # ±5%는 표시상 의미가 거의 없고 설정 파일 churn만 만든다.
                if abs(cap - baseline) <= baseline * 0.05:
                    reasons.append(f"{name}:변화<=5%")
                    continue
            updates[keys[name]] = cap
            out[name] = cap

        if not updates:
            out["skipped"] = "; ".join(reasons) or "변경 없음"
            return out

        target = cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})
        old = {key: target.get(key) for key in updates}
        missing = {key for key in updates if key not in target}
        target.update(updates)
        try:
            cfg.save_yok3x()
        except Exception:
            # 저장 실패 시 메모리 설정만 바뀐 반쪽 상태도 남기지 않는다.
            for key, value in old.items():
                if key in missing:
                    target.pop(key, None)
                else:
                    target[key] = value
            raise
        _CLAUDE_CALIBRATION_STATE[state_key] = now
        out["skipped"] = "; ".join(reasons)
        # yok3x.json을 실제로 바꾸는 부작용이라 사용자에게 보여야 한다. logger.info는 핸들러
        # 미구성 시 삼켜지므로, 코드베이스 관례([reserve]·[acquire]·[guard])대로 print를 쓴다.
        print(f"[calib] claude 자동 캘리브레이션 저장: {updates}", flush=True)
        return out


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
        windows.append(Window("5h", 100.0 * tok5 / cap5, window_minutes=300,
                              used_tokens=tok5, limit_tokens=int(cap5)))
    if cap7 > 0:
        windows.append(Window("7d", 100.0 * tok7 / cap7, window_minutes=10080,
                              used_tokens=tok7, limit_tokens=int(cap7)))
    # 7d 리셋 위상: 실측(oauth/statusline)에서 저장해둔 주간 리셋 앵커(weekly_reset_epoch)가 있으면 주
    # 단위로 전개해 7d 창에 붙인다 → "168시간 롤링" 대신 실제 리셋 카운트다운. 7d 리셋은 고정 주간이라
    # 위상 하나로 안정적으로 유지된다(값이 없으면 롤링 라벨 유지 — 지어내지 않음). 5h는 세션 기반이라 생략.
    anchor = _num(conf.get("weekly_reset_epoch"))
    if anchor:
        r = anchor
        while r <= now:
            r += 7 * 86400.0
        for w in windows:
            if w.name == "7d":
                w.resets_at = r
    detail = (f"5h {tok5:,}tok" + (f"/{int(cap5):,}" if cap5 else "")
              + f", 7d {tok7:,}tok" + (f"/{int(cap7):,}" if cap7 else ""))
    if not windows:
        return LimitReading(backend, "claude_transcripts", ok=False, real=False,
                            detail=detail + " (cap 미설정)",
                            error="limits.claude.limit_5h_tokens/limit_7d_tokens 미설정")
    return LimitReading(backend, "claude_transcripts", ok=True, real=False,
                        windows=windows, detail=detail)


# ------------------------------------ claude (statusline: Claude Code stdin JSON — 1st-party·안전)
# F-08 / R-01b: Claude Code가 statusLine 명령에 **stdin으로** 넘기는 JSON의 rate_limits를 수동 소비한다.
# OAuth 토큰·Anthropic 엔드포인트를 안 건드리는 컴플라이언트 경로(2026-04-04 정책 안전, codex 권고).
# 공식 스키마(code.claude.com/docs/en/statusline): rate_limits.five_hour.{used_percentage, resets_at(epoch초)}
# · rate_limits.seven_day.{...}. Pro/Max·세션 첫 API 응답 후에만 등장하고 각 창 독립 누락 가능
# → 없음/만료 시 로컬 트랜스크립트 추정으로 폴백. `yok3x statusline`이 캐시를 쓰고 이 프로브가 읽는다.

_SL_WINDOWS = (("five_hour", 300, "5h"), ("seven_day", 10080, "7d"))


def _statusline_path(conf: dict[str, Any]) -> Path:
    """rate_limits 캐시 경로. 계정 단위 데이터라 기본은 **사용자 홈**(cwd 무관 — Claude Code가 어느
    프로젝트에서 호출하든 프로브와 같은 파일을 본다). conf.statusline_path로 재정의(테스트)."""
    p = conf.get("statusline_path")
    return Path(p) if p else (Path.home() / ".yok3x" / "statusline.json")


def _extract_statusline_windows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """statusLine stdin JSON → 창 목록. rate_limits 없거나 각 창 누락은 조용히 건너뛴다(공식: 세션 첫
    API 응답 전·비 Pro/Max엔 없음, 지어내지 않음). used_percentage(0~100)·resets_at(epoch초)만 신뢰."""
    rl = data.get("rate_limits")
    if not isinstance(rl, dict):
        return []
    out: list[dict[str, Any]] = []
    for key, mins, name in _SL_WINDOWS:
        seg = rl.get(key)
        if not isinstance(seg, dict):
            continue
        up = _num(seg.get("used_percentage"))
        if up is None:                       # 스키마 변동 방어(카멜 대체 키)
            up = _num(seg.get("usedPercent"))
        if up is None:
            continue
        reset = _num(seg.get("resets_at"))
        if reset is None:                    # 공식은 epoch 정수지만 ISO도 관용 수용
            reset = _parse_iso(seg.get("resets_at"))
        # 위생검사: 5h/7d 창 리셋은 현재로부터 수시간~수일 내. 밀리초 오인(초의 1000배)·자리표시자
        # (9999999999) 등 비현실적 값(과거 1일 이전 / 미래 9일 이후)은 신뢰 불가 → None(롤링 라벨 폴백).
        if reset is not None:
            _now = time.time()
            if reset < _now - 86400 or reset > _now + 9 * 86400:
                reset = None
        out.append({"name": name, "used_percent": float(up),
                    "resets_at": reset, "window_minutes": mins})
    return out


def statusline_capture(conf: dict[str, Any], raw: str) -> str:
    """`yok3x statusline` 핸들러 본체: Claude Code stdin JSON에서 rate_limits를 뽑아 캐시에 원자적 저장하고
    짧은 상태줄 문자열을 반환(호출측이 stdout 출력). 파싱 실패·rate_limits 부재도 안전(빈 창)."""
    try:
        data = json.loads(raw) if raw and raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}
    windows = _extract_statusline_windows(data) if isinstance(data, dict) else []
    payload = {"captured_at": time.time(), "windows": windows}
    try:
        path = _statusline_path(conf)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.{os.getpid()}.tmp"   # pid 고유 — 동시 렌더 시 공유 tmp 경합 방지
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
    if windows:
        body = " · ".join(f"{w['name']} {w['used_percent']:.0f}%" for w in windows)
    else:
        body = "usage 대기(첫 응답 후)"
    return f"yok3x {body}"


def _probe_claude_statusline(backend: str, conf: dict[str, Any]) -> LimitReading:
    """F-08 프로브: `yok3x statusline`이 캐시한 Claude Code rate_limits를 읽는다(1st-party·안전).
    신선하고 창이 있으면 real=True(리셋시각 포함). 없음/만료면 트랜스크립트 추정으로 폴백."""
    max_stale = float(conf.get("statusline_max_stale_sec", 900))
    payload: Any = None
    try:
        payload = json.loads(_statusline_path(conf).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        cap_at = _num(payload.get("captured_at"))
        age = (time.time() - cap_at) if cap_at else float("inf")
        windows = [Window(name=w.get("name", "?"),
                          used_percent=float(_num(w.get("used_percent")) or 0.0),
                          resets_at=_num(w.get("resets_at")),
                          window_minutes=_int(w.get("window_minutes")))
                   for w in (payload.get("windows") or [])
                   if isinstance(w, dict) and _num(w.get("used_percent")) is not None]
        if windows and age <= max_stale:
            det = " · ".join(f"{w.name} {w.used_percent:.0f}%" for w in windows)
            return LimitReading(backend, "claude_statusline", ok=True, real=True,
                                windows=windows, detail=f"{det} (statusline {int(age)}s전)")
    est = _probe_claude_transcripts(backend, conf)   # 폴백: 로컬 트랜스크립트 추정
    # 미보정 추정은 캐시read까지 세어 과대(수백~수천%)해질 수 있다. 새 프로젝트는 캡이 plan 프리셋뿐이라
    # 실측 7d 3%인데도 995%로 표시되고, 가드가 그 값으로 **모든 런을 stop**시킨다(실측 확인).
    # oauth 경로와 동일하게 비현실적으로 높으면 신뢰하지 않고 원장(sane) 폴백에 맡긴다(BUG-15 교훈).
    if est.ok and est.ratio() > 2.0:
        return LimitReading(
            backend, "claude_statusline", ok=False, real=False,
            error=(f"추정 사용률 비현실적({est.ratio() * 100:.0f}%) — 캡 미보정으로 판단해 무시. "
                   "`yok3x calibrate claude 7d <실제%>` 또는 limits.claude.plan 설정 권장"))
    if est.ok:
        est.detail += "  (statusline 없음/만료 → 추정)"
    return est


# 파싱 결과 캐시: path -> (mtime, size, [(ts, tokens), ...]). 트랜스크립트 JSONL은 append-only라
# mtime·size가 그대로면 재파싱이 불필요하다. 한 번의 build_state가 여러 창(5h·7d·오늘·since-reset)을
# 질의하며 매번 전 파일을 read+json.loads 하던 게 병목(build_state 13초)이었다 — 파싱을 파일당 1회로
# 줄이고 창 질의는 메모리의 이벤트를 cutoff로 필터만 한다(사용자 지적: 대시보드가 느려 저장이 안 되는 듯).
_TRANSCRIPT_EVENT_CACHE: dict[str, tuple[float, int, list[tuple[float, int]]]] = {}


def _file_usage_events(f: Path) -> list[tuple[float, int]]:
    """파일의 (timestamp, 토큰합) 이벤트 목록. mtime·size 불변이면 캐시 재사용(재파싱 안 함)."""
    try:
        stt = f.stat()
    except OSError:
        return []
    key = str(f)
    hit = _TRANSCRIPT_EVENT_CACHE.get(key)
    if hit and hit[0] == stt.st_mtime and hit[1] == stt.st_size:
        return hit[2]
    events: list[tuple[float, int]] = []
    try:
        text = f.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_iso(d.get("timestamp"))
        if ts is None:
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        u = msg.get("usage") or d.get("usage") or {}
        if not isinstance(u, dict):
            continue
        events.append((ts, _int(u.get("input_tokens")) + _int(u.get("output_tokens"))
                       + _int(u.get("cache_creation_input_tokens"))
                       + _int(u.get("cache_read_input_tokens"))))
    _TRANSCRIPT_EVENT_CACHE[key] = (stt.st_mtime, stt.st_size, events)
    return events


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
        except OSError:
            continue
        for ts, tok in _file_usage_events(f):
            if ts >= cutoff:
                total += tok
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
