# 워커 산출물(텍스트) → 실제 파일 게시. 설계 근거: codex 공동검토 2026-07-17.
#
# 왜 이 모듈이 있나: 워커는 안전상 **텍스트 생산자**다(claude는 --disallowedTools로 Write/Edit 차단).
# 그래서 "이 폴더에 계산기 만들어줘"를 시켜도 코드는 답변 텍스트로만 나오고 폴더엔 아무것도 안 생겼다.
# 워커 권한을 여는 건 로드맵 H(MCP)의 큰 작업이므로, 그 전까지는 **오케스트레이터가 대신 쓴다**.
#
# 핵심 설계 결정(codex 반박 수용):
#   · 코드블록에서 **파일명을 추측하지 않는다** — 데모용 휴리스틱이라 위험. 워커가 펜스 info에
#     `file:<상대경로>`로 **명시**한 것만 게시한다. 명시 없으면 저장하지 않는다(텍스트로만 남김).
#   · 경로는 전부 검증: 상대경로만·정규화 후 루트 내부 재확인·`..`/절대/드라이브/UNC/심볼릭 거부·
#     중복/대소문자 충돌 거부·덮어쓰기 기본 금지·개수/크기 상한.
#   · 이 모듈은 **순수**(파일시스템 쓰기 없음) — 파싱·검증만. 실제 게시는 orchestrator가 임시dir→원자적 교체.
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# ```file:상대/경로.ext  ... ``` — 언어가 아니라 경로를 info string에 명시하게 한다.
# (```html 같은 언어 펜스는 '추측'이 되므로 대상이 아니다.)
_FILE_FENCE_RE = re.compile(
    r"^[ \t]*```[ \t]*file:[ \t]*(?P<path>[^\n`]+?)[ \t]*\r?\n(?P<body>.*?)^[ \t]*```[ \t]*\r?$",
    re.M | re.S)

# 워커 프롬프트에 주입할 출력 계약. 경로를 명시하게 만드는 것이 이 기능의 안전 근거다.
FILES_CONTRACT = (
    "[산출물 파일] 만든 파일은 각각 아래 형식으로 **경로를 명시**해 제출하라(경로 없는 코드블록은 "
    "저장되지 않는다). 경로는 작업 루트 기준 **상대경로**여야 하고 `..`·절대경로는 금지다.\n"
    "```file:index.html\n<!doctype html>\n...\n```\n"
    "```file:src/app.js\n...\n```\n"
    "설명은 블록 밖에 쓰고, 블록 안에는 **파일 내용만** 넣어라(생략·요약 금지 — 그대로 저장된다).")


@dataclass
class FileBlock:
    path: str            # 워커가 명시한 원본 경로(정규화 전)
    content: str

    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass
class Plan:
    """게시 계획. accepted만 쓰고, rejected는 사유와 함께 감사 로그에 남긴다."""
    accepted: list[FileBlock] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)   # {path, reason}


def parse_file_blocks(text: str) -> list[FileBlock]:
    """`​```file:<path>` 펜스만 추출한다. 경로 미명시 블록은 애초에 잡지 않는다(추측 금지)."""
    out: list[FileBlock] = []
    for m in _FILE_FENCE_RE.finditer(text or ""):
        body = m.group("body")
        if body.endswith("\r\n"):
            body = body[:-2]                 # 닫는 펜스 앞 CRLF는 내용이 아니다
        elif body.endswith("\n"):
            body = body[:-1]                 # 닫는 펜스 앞 LF는 내용이 아니다
        out.append(FileBlock(path=m.group("path").strip(), content=body))
    return out


# 윈도우 예약 장치명 — 확장자가 붙어도 예약이다(CON.txt 등). 크로스플랫폼 안전 위해 어디서나 거부.
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def _bad_path(raw: str) -> str:
    """경로가 거부돼야 하면 사유, 통과면 빈 문자열. 문자열 단계에서 1차 차단한다."""
    p = (raw or "").strip().replace("\\", "/")
    if not p:
        return "빈 경로"
    if p.startswith("/") or p.startswith("//"):
        return "절대경로/UNC 금지"
    if re.match(r"^[A-Za-z]:", p):
        return "드라이브 경로 금지"
    if p.endswith("/"):
        return "디렉터리만 지정(파일명 없음)"
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts:
        return "빈 경로"
    if any(seg == ".." for seg in parts):
        return ".. 상위 이동 금지"
    for seg in parts:
        if seg != seg.strip():
            return "세그먼트 앞뒤 공백 금지"
        if seg.endswith(".") or seg.endswith(" "):
            return "윈도우에서 위험한 이름(끝의 공백/점)"
        if seg.split(".")[0].lower() in _WIN_RESERVED:
            return f"윈도우 예약 장치명({seg})"
    if re.search(r'[<>:"|?*\x00-\x1f]', p):
        return "허용되지 않는 문자"
    return ""


def validate_relative_path(raw: str) -> str:
    """공개 경로 검증 API. 안전하면 빈 문자열, 위험하면 거부 사유를 돌려준다."""
    return _bad_path(raw)


def plan_files(blocks: list[FileBlock], *, existing: set[str] | None = None,
               overwrite: bool = False, max_files: int = 20,
               max_bytes_per_file: int = 512_000, max_total_bytes: int = 2_000_000) -> Plan:
    """블록들을 검증해 게시 계획을 만든다. **순수** — 파일시스템을 건드리지 않는다.

    existing: 이미 대상 루트에 있는 상대경로(소문자) 집합. overwrite=False면 충돌을 거부한다.
    """
    plan = Plan()
    seen: dict[str, str] = {}          # 소문자 경로 → 원본(대소문자 충돌 탐지)
    total = 0
    have = {s.lower() for s in (existing or set())}
    for b in blocks:
        reason = _bad_path(b.path)
        if reason:
            plan.rejected.append({"path": b.path, "reason": reason})
            continue
        norm = "/".join(seg for seg in b.path.strip().replace("\\", "/").split("/")
                        if seg not in ("", "."))
        key = norm.lower()
        if key in seen:
            plan.rejected.append({"path": b.path, "reason": f"중복/대소문자 충돌({seen[key]})"})
            continue
        if not overwrite and key in have:
            plan.rejected.append({"path": b.path, "reason": "기존 파일 덮어쓰기 금지"})
            continue
        size = len(b.content.encode("utf-8"))
        if size > max_bytes_per_file:
            plan.rejected.append({"path": b.path, "reason": f"파일 크기 초과({size}B)"})
            continue
        if len(plan.accepted) >= max_files:
            plan.rejected.append({"path": b.path, "reason": f"파일 개수 상한({max_files}) 초과"})
            continue
        if total + size > max_total_bytes:
            plan.rejected.append({"path": b.path, "reason": "전체 크기 상한 초과"})
            continue
        seen[key] = norm
        total += size
        plan.accepted.append(FileBlock(path=norm, content=b.content))
    return plan
