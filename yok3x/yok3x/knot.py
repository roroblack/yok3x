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


def _recency_weight(created: str, now: datetime, halflife_days: float) -> float:
    """최신성 감쇠 가중(0~1). 반감기마다 절반. halflife<=0이거나 파싱 실패면 1.0(감쇠 없음).
    Mem0의 '낡은 기억 강등'을 의존성 0으로 근사 — 검색 점수에 곱한다."""
    if halflife_days <= 0 or not created:
        return 1.0
    try:
        dt = datetime.fromisoformat(str(created).strip())
    except Exception:
        return 1.0
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return 0.5 ** (age_days / halflife_days)


def _similarity(a: dict, b: dict) -> float:
    """두 노트의 유사도(0~1) — 태그·링크·제목토큰 자카드의 평균. 임베딩 없이 그래프/텍스트만."""
    def toks(n):
        title = re.sub(r"[^\w가-힣 ]", " ", str(n.get("title", "")).lower())
        return set(w for w in title.split() if len(w) > 1)
    def links(n):
        return set(l.lower().strip() for l in LINK_RE.findall(n.get("body", "")))
    parts = []
    for sa, sb in ((set(a.get("tags", [])), set(b.get("tags", []))),
                   (links(a), links(b)), (toks(a), toks(b))):
        if sa or sb:
            parts.append(len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0)
    return sum(parts) / len(parts) if parts else 0.0


def extract_key_points(text: str, max_points: int = 8) -> str:
    """요점 추출(Mem0식 consolidation, 의존성 0). 원문 전체 대신 SELF-CHECK·결정·결함 등
    '결론 신호' 줄만 남긴다 — 새 LLM 호출 없이 워커가 이미 낸 구조를 재사용."""
    sig = ("self-check", "score", "결함", "결정", "수정", "요약", "결론", "todo",
           "버그", "fix", "decision", "- [", "* ")
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    picked = [l for l in lines if any(s in l.lower() for s in sig)]
    if not picked:                       # 신호 없으면 앞 몇 줄로 폴백
        picked = lines[:max_points]
    return "\n".join(picked[:max_points])


def query(cfg: Config, q: str, limit: int = 5, expand: bool = True) -> list[tuple[float, dict]]:
    """이중레벨 검색(LightRAG식, 의존성 0).

    저수준: 키워드 스코어링(제목 3·태그 2·본문 1/히트). 고수준: 상위 직접 히트에서
    [[위키링크]]·공유 태그로 '연결된 노트'까지 확장(키워드가 직접 안 맞아도 개념적으로
    관련된 것을 끌어온다). 확장 점수는 원점수의 할인값이라 직접 히트가 상위에 남는다.
    expand=False면 키워드만(구동작).
    """
    terms = [t.lower() for t in q.split() if t.strip()]
    notes = _load_notes(cfg)
    now = datetime.now()
    halflife = float((cfg.yok3x.get("knot") or {}).get("recency_halflife_days", 0) or 0)
    # 링크 해소용 인덱스: 제목/파일stem(소문자) → 노트
    by_key: dict[str, dict] = {}
    for n in notes:
        by_key.setdefault(str(n.get("title", "")).lower().strip(), n)
        by_key.setdefault(n["path"].stem.lower(), n)
    scores: dict[Path, list] = {}   # path → [score, note]
    for n in notes:                 # 저수준: 키워드 × 최신성 감쇠
        title = str(n.get("title", "")).lower()
        tags = " ".join(n.get("tags", [])).lower()
        body = n.get("body", "").lower()
        s = sum(3 * title.count(t) + 2 * tags.count(t) + min(body.count(t), 5) for t in terms)
        if s > 0:
            s *= _recency_weight(n.get("created", ""), now, halflife)   # 낡은 기억 강등
            scores[n["path"]] = [float(s), n]
    if expand and scores:           # 고수준: 링크·태그 그래프 확장
        for base, n in sorted(scores.values(), key=lambda x: -x[0])[:3]:
            for link in LINK_RE.findall(n.get("body", "")):
                tgt = by_key.get(link.lower().strip())
                if tgt is not None and tgt["path"] != n["path"]:
                    hit = scores.setdefault(tgt["path"], [0.0, tgt])
                    hit[0] = max(hit[0], base * 0.4)          # 링크된 노트
            ntags = set(n.get("tags", []))
            if ntags:
                for m in notes:
                    shared = ntags & set(m.get("tags", []))
                    if shared and m["path"] != n["path"]:
                        hit = scores.setdefault(m["path"], [0.0, m])
                        hit[0] = max(hit[0], base * 0.25 * len(shared))   # 공유 태그
    out = sorted(scores.values(), key=lambda x: -x[0])
    return [(sc, n) for sc, n in out[:limit]]


def lint(cfg: Config) -> list[str]:
    """frontmatter 누락 필드·깨진 [[위키링크]] 점검. 자동 저장된 런 노트(source=orchestrator)는
    사용자 큐레이션 대상이 아니라 이력이므로 lint에서 제외한다(워커 출력의 [[..]]는 실제 링크가
    아닌 텍스트라 깨진 링크로 오탐되던 문제)."""
    issues: list[str] = []
    notes = _load_notes(cfg)
    titles = {str(n.get("title", "")).lower() for n in notes}
    stems = {n["path"].stem.lower() for n in notes}
    curated = [n for n in notes if n.get("source") != "orchestrator"]
    for n in curated:
        rel = n["path"].name
        for field in ("id", "title", "created"):
            if field not in n:
                issues.append(f"{rel}: frontmatter '{field}' 누락")
        for link in LINK_RE.findall(n.get("body", "")):
            l = link.lower().strip()
            if l not in titles and l not in stems:
                issues.append(f"{rel}: 깨진 링크 [[{link}]]")
    # 중복 통합(Mem0식): 유사도 임계 이상인 노트 쌍을 병합 후보로 표시(임베딩 없이 태그·링크·제목).
    thr = float((cfg.yok3x.get("knot") or {}).get("dedup_threshold", 0.6) or 0.6)
    for i in range(len(curated)):                    # 런 노트 제외 — 자동 이력은 중복이 정상
        for j in range(i + 1, len(curated)):
            sim = _similarity(curated[i], curated[j])
            if sim >= thr:
                issues.append(f"{curated[i]['path'].name} ~ {curated[j]['path'].name}: "
                              f"중복 후보(유사도 {sim:.0%}) — 통합 검토")
    return issues


def context_for_prompt(cfg: Config, task: str, limit: int = 3) -> str:
    """작업과 관련된 '사용자 큐레이션' 노트를 프롬프트에 주입할 블록으로 만든다.
    자동 저장된 런 노트(source=orchestrator)는 주입하지 않는다 — 직전 실패 출력이 다음 런에
    주입돼 워커가 그대로 따라하는 자기오염 루프를 막기 위함(이력·검색용으로는 knot에 남아 있음)."""
    hits = [(s, n) for s, n in query(cfg, task, limit=limit * 3)
            if n.get("source") != "orchestrator"][:limit]
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
