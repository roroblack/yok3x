"""knot — 지식그물. 평문 마크다운으로 에이전트끼리 기억을 공유한다.

파일 형식(knowledge/*.md):
    ---
    id: 20260704-1a2b3c
    title: ...
    tags: [tag1, tag2]
    source: worker명 또는 user
    created: ISO8601
    ---
    본문. [[다른 노트 제목]] 위키링크 지원.

명령: save / ingest / query / lint
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import Config

FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _slug(s: str) -> str:
    s = re.sub(r"[^\w가-힣\- ]", "", s).strip().replace(" ", "-")
    return s[:60] or "note"


def save(cfg: Config, title: str, body: str, tags: list[str] | None = None,
         source: str = "user") -> Path:
    cfg.ensure_dirs()
    now = datetime.now()
    nid = now.strftime("%Y%m%d-") + hashlib.sha256(f"{title}{body}".encode()).hexdigest()[:6]
    tags = tags or []
    fm = (f"---\n"
          f"id: {nid}\n"
          f"title: {title}\n"
          f"tags: [{', '.join(tags)}]\n"
          f"source: {source}\n"
          f"created: {now.isoformat(timespec='seconds')}\n"
          f"---\n\n")
    path = cfg.paths.knowledge / f"{_slug(title)}-{nid[-6:]}.md"
    path.write_text(fm + body.rstrip() + "\n", encoding="utf-8")
    return path


def ingest(cfg: Config, src: str | Path) -> list[Path]:
    """외부 md 파일/디렉터리를 knowledge/로 가져온다. frontmatter 없으면 생성."""
    cfg.ensure_dirs()
    src = Path(src)
    files = sorted(src.rglob("*.md")) if src.is_dir() else [src]
    out: list[Path] = []
    for f in files:
        text = f.read_text(encoding="utf-8-sig")
        if FM_RE.match(text):
            dst = cfg.paths.knowledge / f.name
            shutil.copy2(f, dst)
            out.append(dst)
        else:
            title = f.stem
            out.append(save(cfg, title, text, tags=["ingested"], source=str(f)))
    return out


def _load_notes(cfg: Config) -> list[dict]:
    notes = []
    if not cfg.paths.knowledge.exists():
        return notes
    for f in sorted(cfg.paths.knowledge.glob("*.md")):
        text = f.read_text(encoding="utf-8-sig")
        m = FM_RE.match(text)
        meta: dict = {"path": f, "title": f.stem, "tags": [], "body": text}
        if m:
            meta["body"] = text[m.end():]
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k == "tags":
                        meta["tags"] = [t.strip() for t in v.strip("[]").split(",") if t.strip()]
                    else:
                        meta[k] = v
        notes.append(meta)
    return notes


def query(cfg: Config, q: str, limit: int = 5) -> list[tuple[float, dict]]:
    """키워드 스코어링 검색: 제목 3점, 태그 2점, 본문 1점/히트."""
    terms = [t.lower() for t in q.split() if t.strip()]
    scored = []
    for n in _load_notes(cfg):
        s = 0.0
        title = str(n.get("title", "")).lower()
        tags = " ".join(n.get("tags", [])).lower()
        body = n.get("body", "").lower()
        for t in terms:
            s += 3 * title.count(t) + 2 * tags.count(t) + min(body.count(t), 5)
        if s > 0:
            scored.append((s, n))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


def lint(cfg: Config) -> list[str]:
    """frontmatter 누락 필드·깨진 [[위키링크]] 점검."""
    issues: list[str] = []
    notes = _load_notes(cfg)
    titles = {str(n.get("title", "")).lower() for n in notes}
    stems = {n["path"].stem.lower() for n in notes}
    for n in notes:
        rel = n["path"].name
        for field in ("id", "title", "created"):
            if field not in n:
                issues.append(f"{rel}: frontmatter '{field}' 누락")
        for link in LINK_RE.findall(n.get("body", "")):
            l = link.lower().strip()
            if l not in titles and l not in stems:
                issues.append(f"{rel}: 깨진 링크 [[{link}]]")
    return issues


def context_for_prompt(cfg: Config, task: str, limit: int = 3) -> str:
    """작업과 관련된 노트를 프롬프트에 주입할 블록으로 만든다."""
    hits = query(cfg, task, limit=limit)
    if not hits:
        return ""
    parts = ["## 공유 기억(knot)"]
    for score, n in hits:
        body = n.get("body", "").strip()
        parts.append(f"### {n.get('title')}\n{body[:600]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- context.md / brief.md

def clip(text: str, max_chars: int) -> str:
    """글자 제한: 초과분은 앞 70%/뒤 30% 보존 방식으로 중간을 잘라낸다."""
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.7)
    tail = max_chars - head - 20
    return text[:head] + "\n…(글자 제한으로 중간 생략)…\n" + text[-tail:]


def write_context(cfg: Config, content: str) -> Path:
    p = cfg.paths.root / "context.md"
    p.write_text(clip(content, int(cfg.yok3x["context_max_chars"])), encoding="utf-8")
    return p


def write_brief(cfg: Config, content: str) -> Path:
    p = cfg.paths.root / "brief.md"
    p.write_text(clip(content, int(cfg.yok3x["brief_max_chars"])), encoding="utf-8")
    return p


def read_context(cfg: Config) -> str:
    p = cfg.paths.root / "context.md"
    return p.read_text(encoding="utf-8-sig") if p.exists() else ""


def read_brief(cfg: Config) -> str:
    p = cfg.paths.root / "brief.md"
    return p.read_text(encoding="utf-8-sig") if p.exists() else ""
