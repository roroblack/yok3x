"""backends.json 어댑터 실행기.

CLI 호출 방식(검증된 공식 사양):
  claude : claude -p "<prompt>" --output-format json
           → 단일 JSON. result / session_id / total_cost_usd / usage 포함.
  codex  : codex exec --json "<prompt>"
           → JSONL 이벤트 스트림. item_type이 agent_message/assistant_message인
             item.completed의 text가 최종 응답. usage가 실린 이벤트에서 토큰 합산.
  gemini : gemini -p "<prompt>" --output-format json
           → 단일 JSON {response, stats.models[*].tokens...}.
             JSON 앞에 'Loaded cached credentials.' 등 노이즈가 붙을 수 있어
             첫 '{'부터 파싱한다.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackendResult:
    backend: str
    ok: bool
    text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    raw_excerpt: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def run_backend(name: str, spec: dict[str, Any], prompt: str,
                cwd: str | None = None, model: str | None = None) -> BackendResult:
    btype = spec.get("type", "cli")
    t0 = time.time()
    if btype == "mock":
        res = _run_mock(name, spec, prompt)
    elif btype == "cli":
        res = _run_cli(name, spec, prompt, cwd=cwd, model=model)
    elif btype == "native":
        res = BackendResult(backend=name, ok=False,
                            error="native(HTTP API) 어댑터는 endpoint 설정 후 사용. backends.json 참조.")
    elif btype == "mcp":
        res = BackendResult(backend=name, ok=False,
                            error="mcp 어댑터는 MCP 클라이언트 환경(Claude Code 등)에서 서버 등록 후 사용.")
    else:
        res = BackendResult(backend=name, ok=False, error=f"unknown backend type: {btype}")
    res.duration_ms = int((time.time() - t0) * 1000)
    return res


# ---------------------------------------------------------------- CLI

def _run_cli(name: str, spec: dict[str, Any], prompt: str,
             cwd: str | None = None, model: str | None = None) -> BackendResult:
    cmd = [str(a).replace("{prompt}", prompt) for a in spec["command"]]
    # 모델 다운그레이드(적응형 열화): model이 주어지고 model_arg 템플릿이 있으면 덧붙인다.
    if model and spec.get("model_arg"):
        cmd += [str(a).replace("{model}", model) for a in spec["model_arg"]]
    # Windows: claude/codex/gemini는 npm .cmd 심 — CreateProcess가 PATHEXT를
    # 해석하지 않으므로 shutil.which로 실제 경로(claude.cmd 등)로 치환한다.
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd[0] = resolved
    timeout = int(spec.get("timeout_sec", 600))
    try:
        # stdin=DEVNULL: 프롬프트는 argv({prompt})로 넘기므로 stdin 불필요. 이를 막지 않으면
        # 헤드리스 실행 중 CLI가 인증·온보딩 등으로 대화형 입력을 기다릴 때 상속된 stdin에서
        # 데드락(→ timeout까지 멈춤)한다. DEVNULL로 즉시 EOF → 대화형이면 빠르게 실패한다.
        # encoding=utf-8: Windows 기본(cp949) 디코딩이 CLI의 UTF-8 JSON을 깨뜨리지 않게.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=cwd or None, stdin=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return BackendResult(backend=name, ok=False,
                             error=f"실행 파일 없음: {cmd[0]!r} — 해당 CLI를 설치하거나 backends.json에서 "
                                   f"type을 'mock'으로 바꿔 드라이런 가능. (cmd: {shlex.join(cmd)})")
    except subprocess.TimeoutExpired:
        return BackendResult(backend=name, ok=False, error=f"timeout {timeout}s: {shlex.join(cmd)}")

    out, err = proc.stdout, proc.stderr
    parser = spec.get("parser", "raw")
    try:
        if parser == "claude_json":
            res = _parse_claude(out)
        elif parser == "codex_jsonl":
            res = _parse_codex(out)
        elif parser == "gemini_json":
            res = _parse_gemini(out)
        else:
            res = BackendResult(backend=name, ok=True, text=out.strip())
    except Exception as e:  # 파싱 실패 → 원문 보존
        res = BackendResult(backend=name, ok=proc.returncode == 0, text=out.strip(),
                            error=f"parse error: {e}")
    res.backend = name
    if proc.returncode != 0 and not res.error:
        res.ok = False
        res.error = f"exit {proc.returncode}: {err.strip()[:500]}"
    res.raw_excerpt = (out[:800] + ("…" if len(out) > 800 else ""))
    return res


def _parse_claude(out: str) -> BackendResult:
    data = json.loads(out[out.index("{"):])
    usage = data.get("usage") or {}
    it = int(usage.get("input_tokens", 0) or 0)
    ot = int(usage.get("output_tokens", 0) or 0)
    return BackendResult(
        backend="claude",
        ok=not data.get("is_error", False),
        text=str(data.get("result", "")).strip(),
        cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
        input_tokens=it, output_tokens=ot, total_tokens=it + ot,
        meta={"session_id": data.get("session_id"), "num_turns": data.get("num_turns")},
    )


def _parse_codex(out: str) -> BackendResult:
    text, it, ot, tot = "", 0, 0, 0
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = ev.get("item") or {}
        # codex 0.144: item.completed 이벤트의 item.type=="agent_message"·item.text.
        # 구형은 item.item_type 이었다 — 둘 다 인식(호환).
        itype = item.get("type") or item.get("item_type")
        if itype in ("agent_message", "assistant_message") and item.get("text"):
            text = item["text"]  # 마지막 agent 메시지 채택
        for holder in (ev, ev.get("usage") or {}, item.get("usage") or {}):
            if isinstance(holder, dict) and "input_tokens" in holder:
                it += int(holder.get("input_tokens", 0) or 0)
                ot += int(holder.get("output_tokens", 0) or 0)
    tot = it + ot
    if not text:
        # --json이 아닌 형태로 실행됐거나 이벤트 미검출 → stdout 마지막 비어있지 않은 줄 사용
        lines = [l for l in out.splitlines() if l.strip()]
        text = lines[-1] if lines else ""
    return BackendResult(backend="codex", ok=bool(text), text=text.strip(),
                         input_tokens=it, output_tokens=ot, total_tokens=tot)


def _parse_gemini(out: str) -> BackendResult:
    i = out.index("{")
    data = json.loads(out[i:])
    tot = it = ot = 0
    for m in (data.get("stats", {}).get("models") or {}).values():
        tk = m.get("tokens") or {}
        it += int(tk.get("prompt", 0) or 0)
        ot += int(tk.get("candidates", 0) or 0)
        tot += int(tk.get("total", 0) or 0)
    return BackendResult(backend="gemini", ok=data.get("response") is not None,
                         text=str(data.get("response", "")).strip(),
                         input_tokens=it, output_tokens=ot, total_tokens=tot or (it + ot))


# ---------------------------------------------------------------- mock

def _run_mock(name: str, spec: dict[str, Any], prompt: str) -> BackendResult:
    """외부 CLI 없이 전체 파이프라인을 검증하기 위한 결정적 시뮬레이터."""
    time.sleep(float(spec.get("latency_sec", 0.05)))
    h = hashlib.sha256(prompt.encode()).hexdigest()[:8]
    if "SCORE" in prompt or "채점" in prompt or "검수" in prompt or "검토" in prompt:
        score = 6 + int(h, 16) % 4  # 6~9
        text = (f"SCORE: {score}\n"
                f"- [mock:{h}] 구조는 타당. 경계 조건 처리 보강 필요.\n"
                f"- 수정 지시: 입력 검증 1건, 예외 메시지 1건 추가.")
    else:
        text = (f"[mock:{h}] 요청 수행 완료.\n"
                f"요약: {prompt[:120]}…\n"
                f"산출물: (mock 백엔드 — 실제 CLI 연결 시 실산출물로 대체)")
    it, ot = max(50, len(prompt) // 4), max(30, len(text) // 4)
    return BackendResult(backend="mock", ok=True, text=text,
                         cost_usd=round((it + ot) / 1_000_000 * 3.0, 6),
                         input_tokens=it, output_tokens=ot, total_tokens=it + ot)
