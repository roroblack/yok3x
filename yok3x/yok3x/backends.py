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
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
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
                cwd: str | None = None, model: str | None = None,
                effort: str | None = None) -> BackendResult:
    btype = spec.get("type", "cli")
    t0 = time.time()
    if btype == "mock":
        res = _run_mock(name, spec, prompt)
    elif btype == "cli":
        res = _run_cli(name, spec, prompt, cwd=cwd, model=model, effort=effort)
    elif btype in ("openai_http", "native", "local"):
        res = _run_openai_http(name, spec, prompt, model=model)
    elif btype == "mcp":
        res = BackendResult(backend=name, ok=False,
                            error="mcp 어댑터는 MCP 클라이언트 환경(Claude Code 등)에서 서버 등록 후 사용.")
    else:
        res = BackendResult(backend=name, ok=False, error=f"unknown backend type: {btype}")
    res.duration_ms = int((time.time() - t0) * 1000)
    return res


# ---------------------------------------------------------------- CLI

def _run_cli(name: str, spec: dict[str, Any], prompt: str,
             cwd: str | None = None, model: str | None = None,
             effort: str | None = None) -> BackendResult:
    template = spec["command"]
    has_prompt_arg = any("{prompt}" in str(a) for a in template)
    # BUG-18 방어(BUG-10 재발 차단): 멀티라인 프롬프트를 argv({prompt})로 넘기면 Windows npm .cmd
    # 심이 첫 줄바꿈에서 argv를 잘라 워커가 첫 줄([작업])만 받는다. 스테일 backends.json이 옛 {prompt}
    # 형식이어도, 멀티라인이면 {prompt} 자리를 빼고 stdin으로 넘겨 잘림을 원천 차단한다.
    if has_prompt_arg and "\n" in prompt:
        template = [a for a in template if "{prompt}" not in str(a)]
        has_prompt_arg = False
    cmd = [str(a).replace("{prompt}", prompt) for a in template]
    # 모델 다운그레이드(적응형 열화): model이 주어지고 model_arg 템플릿이 있으면 덧붙인다.
    if model and spec.get("model_arg"):
        cmd += [str(a).replace("{model}", model) for a in spec["model_arg"]]
    # 추론 강도(effort): effort가 주어지고 effort_arg 템플릿이 있는 backend만 덧붙인다.
    # claude=--effort <level>, codex=-c model_reasoning_effort=<level>. gemini는 미지원(무시).
    if effort and spec.get("effort_arg"):
        cmd += [str(a).replace("{effort}", effort) for a in spec["effort_arg"]]
    # Windows: claude/codex/gemini는 npm .cmd 심 — CreateProcess가 PATHEXT를
    # 해석하지 않으므로 shutil.which로 실제 경로(claude.cmd 등)로 치환한다.
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd[0] = resolved
    timeout = int(spec.get("timeout_sec", 600))
    # 프롬프트 전달: argv에 {prompt}가 없으면 stdin으로 넘긴다. Windows npm .cmd 심은
    # 멀티라인 argv를 첫 줄바꿈에서 잘라버려(cmd.exe 파싱), 여러 줄 프롬프트가 첫 줄만
    # 전달되던 치명 버그가 있었다 — stdin 전달로 우회한다. input=prompt는 프롬프트 후
    # 즉시 EOF라 대화형 대기 데드락도 방지한다. {prompt}가 argv에 있으면(구식) DEVNULL 유지.
    # encoding=utf-8: Windows 기본(cp949)이 CLI의 UTF-8 JSON을 깨뜨리지 않게.
    stdin_kw = {"stdin": subprocess.DEVNULL} if has_prompt_arg else {"input": prompt}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=cwd or None, encoding="utf-8", errors="replace",
                              **stdin_kw)
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


# ---------------------------------------------------------------- OpenAI 호환 HTTP(로컬)

def _run_openai_http(name: str, spec: dict[str, Any], prompt: str,
                     model: str | None = None) -> BackendResult:
    """OpenAI 호환 /v1/chat/completions 로컬 서버 호출(llama.cpp·LM Studio·vLLM·Ollama /v1 등).
    표준 라이브러리 urllib만 사용(의존성 0). 로컬은 무료라 cost=0. P3 오프라인 폴백의 실행부."""
    base = str(spec.get("base_url", "http://localhost:8000/v1")).rstrip("/")
    mdl = model or spec.get("model") or "local"
    body = json.dumps({
        "model": mdl,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": spec.get("temperature", 0.2),
        "max_tokens": spec.get("max_tokens", 2048),
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = spec.get("api_key")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    timeout = int(spec.get("timeout_sec", 120))
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        return BackendResult(backend=name, ok=False,
                             error=f"로컬 HTTP 실패({base}): {getattr(e, 'reason', e)} — 로컬 서버가 떠 "
                                   f"있는지 확인(base_url 설정).")
    except Exception as e:
        return BackendResult(backend=name, ok=False, error=f"로컬 HTTP 오류: {type(e).__name__}: {e}")
    choices = data.get("choices") or []
    text = ""
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or choices[0].get("text") or ""
    # 추론형(reasoning) 로컬 모델의 <think>…</think> 블록 제거 — 산출물만 남긴다.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    usage = data.get("usage") or {}
    it = int(usage.get("prompt_tokens", 0) or 0)
    ot = int(usage.get("completion_tokens", 0) or 0)
    return BackendResult(backend=name, ok=bool(text), text=text,
                         input_tokens=it, output_tokens=ot, total_tokens=it + ot,
                         cost_usd=0.0, meta={"model": data.get("model", mdl), "local": True})


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
