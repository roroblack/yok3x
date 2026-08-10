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
import signal
import re
import shlex
import shutil
import subprocess
import time
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# effort를 지정하지 않으면 yok3x는 --effort/-c 플래그를 **아예 보내지 않는다** → 각 CLI의 자체 기본이
# 적용된다. 그 '기본'이 무엇인지는 backend마다 출처가 달라, 알 수 있는 것만 실제로 읽어서 알려준다.
# 모르면 빈 문자열 — 추측해서 표시하지 않는다(§5.5: 근거 없는 값 만들지 않음).
_CODEX_EFFORT_RE = re.compile(r'^\s*model_reasoning_effort\s*=\s*["\']([^"\']+)["\']', re.M)


def effort_defaults() -> dict[str, str]:
    """backend별 '기본 effort'의 실제 출처를 조회한다. 반환: {backend: 값 or ""}.

    - codex: `$CODEX_HOME|~/.codex/config.toml` 의 `model_reasoning_effort`(사용자가 바꿀 수 있음).
      TOML 전체 파싱은 불필요(키 하나) — tomllib는 3.11+라 3.10 호환 위해 정규식으로 한 줄만 읽는다.
    - claude: CLI/세션이 정하며 도움말·설정에 기본값 명시가 없다 → "" (GUI가 'CLI 기본'으로 표시).
    - gemini/local/mock: effort 미지원.
    """
    out = {"claude": "", "codex": "", "gemini": "", "local": "", "mock": ""}
    home = os.environ.get("CODEX_HOME")
    p = (Path(home) if home else Path.home() / ".codex") / "config.toml"
    try:
        m = _CODEX_EFFORT_RE.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            out["codex"] = m.group(1).strip()
    except OSError:
        pass
    return out


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
                effort: str | None = None, read_only: bool = False,
                mcp_config_path: str | None = None, mcp_allowed_tools: str = "",
                process_started: Callable[[Any], None] | None = None,
                process_finished: Callable[[Any], None] | None = None,
                cancel_event: Any | None = None) -> BackendResult:
    btype = spec.get("type", "cli")
    t0 = time.time()
    if btype == "mock":
        res = _run_mock(name, spec, prompt, cancel_event=cancel_event)
    elif btype == "cli":
        res = _run_cli(name, spec, prompt, cwd=cwd, model=model, effort=effort,
                       read_only=read_only, mcp_config_path=mcp_config_path,
                       mcp_allowed_tools=mcp_allowed_tools, process_started=process_started,
                       process_finished=process_finished, cancel_event=cancel_event)
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

def terminate_process(proc: Any, grace_sec: float = 0.5) -> None:
    """병렬 중단 시 CLI와 그 하위 프로세스를 종료한다(이미 끝났으면 no-op)."""
    try:
        if proc.poll() is not None:
            return
    except (AttributeError, OSError):
        return

    try:
        if os.name == "nt":
            # npm .cmd 심 아래 실제 node CLI까지 /T로 함께 종료한다.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=max(1.0, grace_sec), check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        try:
            proc.terminate()
        except OSError:
            return

    try:
        proc.wait(timeout=grace_sec)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass

def _run_cli(name: str, spec: dict[str, Any], prompt: str,
             cwd: str | None = None, model: str | None = None,
             effort: str | None = None, read_only: bool = False,
             mcp_config_path: str | None = None, mcp_allowed_tools: str = "",
             process_started: Callable[[Any], None] | None = None,
             process_finished: Callable[[Any], None] | None = None,
             cancel_event: Any | None = None) -> BackendResult:
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
    # (effort 기본값 조회는 effort_defaults() 참고 — 아래 조립은 '지정됐을 때'만 플래그를 붙인다)
    # 추론 강도(effort): effort가 주어지고 effort_arg 템플릿이 있는 backend만 덧붙인다.
    # claude=--effort <level>, codex=-c model_reasoning_effort=<level>. gemini는 미지원(무시).
    if effort and spec.get("effort_arg"):
        cmd += [str(a).replace("{effort}", effort) for a in spec["effort_arg"]]
    # ACQUIRE Answerer는 저장소를 조사하되 수정할 수 없어야 한다. backend별 선택 템플릿이
    # 있을 때만 덧붙이고, 없는 커스텀 backend는 기존 argv를 그대로 쓴다.
    if read_only and spec.get("read_only_arg"):
        read_only_args = [str(a) for a in spec["read_only_arg"]]
        # claude 기본 프로필은 일반 워커가 레포를 뒤지지 못하게 모든 도구를 막는다.
        # 조사 프로필의 --disallowedTools가 그 값을 대체해야 Read/Glob/Grep/Bash를 쓸 수 있다.
        if "--disallowedTools" in read_only_args and "--disallowedTools" in cmd:
            old = cmd.index("--disallowedTools")
            del cmd[old:old + 2]
        cmd += read_only_args
    # v4.1.0 MCP 워커도구(a1): mcp_config_path는 orchestrator의 mcp_policy가 화이트리스트·승인
    # 판정을 이미 끝낸 뒤에만 채워진다 — 여기는 그 결과를 argv에 얹기만 한다(정책 판단 없음).
    # backend에 mcp_arg 템플릿이 없으면(codex/gemini 등) 조용히 무시된다(fail-closed).
    if mcp_config_path and spec.get("mcp_arg"):
        mcp_args = [str(a).replace("{mcp_config_path}", mcp_config_path)
                        .replace("{allowed_tools}", mcp_allowed_tools)
                   for a in spec["mcp_arg"]]
        # 도구가 켜지면 기존 전면 차단(--disallowedTools) 대신 --allowedTools 화이트리스트가
        # 통제한다 — 남아 있으면 claude가 두 플래그를 동시에 받아 충돌할 수 있어 제거한다.
        for flag in ("--disallowedTools", "--allowedTools"):
            if flag in cmd:
                old = cmd.index(flag)
                del cmd[old:old + 2]
        cmd += mcp_args
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
    if process_started is None and process_finished is None and cancel_event is None:
        # 단일 호출의 오랜 계약은 그대로 둔다. 병렬 취소 추적이 필요할 때만 Popen을 쓴다.
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
    else:
        if cancel_event is not None and cancel_event.is_set():
            return BackendResult(backend=name, ok=False, error="cancelled before process start")
        popen_kw: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": cwd or None,
            "encoding": "utf-8",
            "errors": "replace",
            "stdin": subprocess.DEVNULL if has_prompt_arg else subprocess.PIPE,
        }
        # 중단 시 CLI가 띄운 하위 프로세스까지 함께 종료할 수 있는 경계를 만든다.
        if os.name == "nt":
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **popen_kw)
        except FileNotFoundError:
            return BackendResult(backend=name, ok=False,
                                 error=f"실행 파일 없음: {cmd[0]!r} — 해당 CLI를 설치하거나 backends.json에서 "
                                       f"type을 'mock'으로 바꿔 드라이런 가능. (cmd: {shlex.join(cmd)})")
        if process_started is not None:
            process_started(proc)
        try:
            out, err = proc.communicate(
                input=None if has_prompt_arg else prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process(proc)
            return BackendResult(backend=name, ok=False,
                                 error=f"timeout {timeout}s: {shlex.join(cmd)}")
        finally:
            if process_finished is not None:
                process_finished(proc)

    if process_started is None and process_finished is None and cancel_event is None:
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

def _run_mock(name: str, spec: dict[str, Any], prompt: str,
              cancel_event: Any | None = None) -> BackendResult:
    """외부 CLI 없이 전체 파이프라인을 검증하기 위한 결정적 시뮬레이터."""
    latency = float(spec.get("latency_sec", 0.05))
    if cancel_event is not None and cancel_event.wait(latency):
        return BackendResult(backend=name, ok=False, error="cancelled")
    time.sleep(latency if cancel_event is None else 0.0)
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
