"""v4.1.0 MCP 워커도구(a1) 정책 계층.

설계(계획서 `docs/plans/v4.1.0-plan-mcp-worker-tools-2026-07-15.md`, codex 안전 리뷰 반영):
yok3x가 MCP 서버 **설정을 워커 CLI에 전달**만 하고, 실제 도구 실행은 워커 CLI(claude 등)의
런타임이 한다(a1). 이 파일은 그 전달을 안전하게 만드는 **정책 판정만** 한다:
opt-in(워커가 명시적으로 요청) · 화이트리스트(전역 `mcp_servers`에 등록된 것만) ·
fail-closed(애매하면 전부 거부) · 감사 로그(무엇이 승인/거부됐는지).

**정직한 한계(a1 vs a2)**: yok3x는 개별 도구 *호출*을 가로채지 않는다(워커 CLI가 직접 실행) —
그래서 인자·경로·호스트 단위 실시간 검증은 이 계층에서 할 수 없다. 감사 로그가 남기는 것도
"이번 호출에 무엇이 *허가*됐는지"이지 "실제로 어떤 도구가 몇 번 *호출*됐는지"가 아니다.
호출 단위 실시간 통제가 필요하면 계획서의 a2(yok3x가 직접 MCP 클라이언트로 호출)가 선행돼야
한다 — 계획서는 a2를 명시적으로 후속(별도 계획)으로 미뤘고, 이 파일도 그 경계를 넘지 않는다.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpGrant:
    """이번 호출에 허용된 MCP 서버·도구. servers가 비어 있으면 도구를 켜지 않는다(안전 기본)."""
    servers: dict[str, Any] = field(default_factory=dict)   # name -> spec(화이트리스트 원본 그대로)
    allow_tools: list[str] = field(default_factory=list)     # 완전정규화 도구명(mcp__server__tool)
    denied_reason: str = ""                                  # 비었으면 왜 비었는지(로그·감사용)

    @property
    def active(self) -> bool:
        return bool(self.servers and self.allow_tools)


def resolve_mcp_grant(cfg_mcp_servers: dict[str, Any], worker_cfg: dict[str, Any]) -> McpGrant:
    """워커의 `mcp_tools` 요청을 전역 화이트리스트(`cfg.yok3x['mcp_servers']`)와 대조해 판정한다.

    fail-closed: 워커가 요청 안 함 / 전역 화이트리스트가 비어있음(opt-in 안 됨) / 요청한 서버가
    화이트리스트에 없음 / 유효한 allow_tools가 하나도 안 남음 — 이 중 하나라도면 예외 없이
    빈 McpGrant(도구 없음)를 낸다. `allow_tools`는 `mcp__<server>__<tool>` 형태만 인정하고,
    **이번 요청에서 실제로 화이트리스트 통과한 서버**에 속한 것만 남긴다(서버 경계를 넘는 도구명
    요청은 조용히 버려진다 — 예: filesystem 서버만 허용됐는데 mcp__other__delete를 적어도 무시).
    """
    req = worker_cfg.get("mcp_tools")
    if not isinstance(req, dict) or not req.get("servers"):
        return McpGrant(denied_reason="워커에 mcp_tools 미설정(opt-in 안 함)")
    if not cfg_mcp_servers:
        return McpGrant(denied_reason="mcp_servers 전역 화이트리스트가 비어있음(opt-in 안 됨)")
    req_servers = req.get("servers") or []
    if not isinstance(req_servers, list):
        return McpGrant(denied_reason="mcp_tools.servers는 리스트여야 함")
    granted_servers: dict[str, Any] = {}
    unknown: list[str] = []
    for name in req_servers:
        spec = cfg_mcp_servers.get(name)
        if spec is None:
            unknown.append(str(name))
            continue
        granted_servers[str(name)] = spec
    if not granted_servers:
        return McpGrant(denied_reason=f"요청 서버가 화이트리스트에 없음: {req_servers}")
    req_tools = req.get("allow_tools") or []
    allow_tools: list[str] = []
    dropped: list[str] = []
    for t in req_tools:
        parts = str(t).split("__", 2)
        if len(parts) == 3 and parts[0] == "mcp" and parts[1] in granted_servers:
            allow_tools.append(str(t))
        else:
            dropped.append(str(t))
    if not allow_tools:
        return McpGrant(denied_reason="화이트리스트 서버는 있으나 유효한 allow_tools 없음(전체 거부)")
    reasons = []
    if unknown:
        reasons.append(f"미승인 서버 요청 무시: {unknown}")
    if dropped:
        reasons.append(f"형식·서버경계 위반 도구명 무시: {dropped}")
    return McpGrant(servers=granted_servers, allow_tools=allow_tools, denied_reason="; ".join(reasons))


def write_mcp_config_file(grant: McpGrant) -> str:
    """claude `--mcp-config`가 읽을 임시 JSON을 만들고 경로를 반환한다. 호출자가 정리(unlink) 책임진다."""
    payload = {"mcpServers": dict(grant.servers)}
    fd, path = tempfile.mkstemp(prefix="yok3x_mcp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def audit_log_path(yok3x_dir: Path) -> Path:
    return yok3x_dir / "mcp_audit.jsonl"


def record_grant(yok3x_dir: Path, *, run_id: str, step: int | None, worker: str, backend: str,
                 grant: McpGrant, timestamp: float | None = None) -> None:
    """이번 호출에 승인(또는 거부)된 MCP 권한을 감사로그(`~/.yok3x/mcp_audit.jsonl`)에 append한다.

    **정직한 한계**: 이 로그는 '무엇이 *허가*됐는지'이지 '실제로 어떤 도구가 몇 번 *호출*됐는지'가
    아니다(모듈 docstring 참고 — a1은 개별 호출을 가로채지 않는다). 실패해도 런을 죽이지 않는다
    (감사 로그는 부가 기능이지 안전장치의 1차 방어선이 아님 — 정책 판정 자체는 이미 끝난 뒤).
    """
    rec = {
        "ts": timestamp if timestamp is not None else time.time(),
        "run_id": run_id, "step": step, "worker": worker, "backend": backend,
        "activated": grant.active,
        "servers": sorted(grant.servers.keys()),
        "allow_tools": list(grant.allow_tools),
        "denied_reason": grant.denied_reason,
    }
    try:
        p = audit_log_path(yok3x_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
