"""v4.6.0 Cognitive Sync Layer — 근거기반 변경 이해(opt-in, 기본 off).

설계 근거: `docs/plans/v4.6.0-plan-cognitive-sync-layer-2026-08-08.md`(codex 공동설계, 2026-08-08).
사용자 요구: "내가 이해하고 따라갈 수 없는 구조가 되면 그 순간 끝이다" — 그래서 이 기능은 없애지
않되, 비용이 드는 부분(light/standard/deep)은 전부 opt-in으로 분리한다(§3.3).

이 모듈(`mode=off`가 쓰는 전부)은 **신규 LLM 호출이 0**이다 — 이미 디스크에 있는 것만 읽어 기계적으로
조립한다: F1-d 검토번들(`changes.diff`/`changes.json`), `run.log`의 라우팅·가드·승인 결정, ACQUIRE의
기계검증된 QA(`acquire.json`). 근거 없는 주장은 억지로 채우지 않고 `OPEN_QUESTION`으로 정직하게
남긴다(codex 경고: "근거 있는 환각" — 근거 링크가 있다고 정확성이 보장되는 게 아니다).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

FACT = "FACT"
RECORDED_DECISION = "RECORDED_DECISION"
INFERENCE = "INFERENCE"
OPEN_QUESTION = "OPEN_QUESTION"
CLAIM_TYPES = (FACT, RECORDED_DECISION, INFERENCE, OPEN_QUESTION)

_DIFF_FILE_RE = re.compile(r'^\+\+\+ b/(.+)$', re.M)
# 추가된(+) 줄에서 def/class 시그니처만 뽑는다 — 문자열·주석 안의 것도 잡힐 수 있어(정규식 근사)
# FACT가 아니라 INFERENCE로 표기한다(아래 build_understanding_bundle 참고).
_SYMBOL_RE = re.compile(r'^\+\s*(?:async\s+)?(?:def|class)\s+(\w+)', re.M)
# run.log의 실제 줄 형식은 `_log()`가 타임스탬프를 앞에 붙인다: "[2026-08-08T12:00:00] [route] ...".
# 그래서 태그 앞에 optional 타임스탬프 대괄호 하나를 허용한다(없어도 매치되게 `?`).
_ROUTE_LOG_RE = re.compile(r'^(?:\[[^\]\n]*\]\s*)?\[(route|degrade|failover|gate)\] (.+)$', re.M)


def _claim_id(*parts: str) -> str:
    """ACQUIRE의 `core_claim()`과 같은 패턴 — 시간/난수 없이 결정적 SHA-1으로 재현성 보장."""
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:16]


def _parse_diff(diff_text: str) -> tuple[list[str], dict[str, list[str]]]:
    """unified diff에서 (변경파일목록, {파일: [추가된 함수/클래스로 보이는 심볼]})을 뽑는다.
    완벽한 AST 파서가 아니다(주석·문자열 안의 `+def`는 걸러내지 못함) — 그래서 심볼 쪽은
    FACT가 아니라 INFERENCE 취급한다. 파일 목록 자체는 diff 헤더에서 직접 나오므로 FACT."""
    files = _DIFF_FILE_RE.findall(diff_text)
    sections = re.split(r'(?=^--- a/)', diff_text, flags=re.M)
    symbols: dict[str, list[str]] = {}
    for sec in sections:
        m = _DIFF_FILE_RE.search(sec)
        if not m:
            continue
        found = _SYMBOL_RE.findall(sec)
        if found:
            symbols[m.group(1)] = sorted(set(found))
    return files, symbols


def _parse_run_log_decisions(log_text: str) -> list[dict[str, str]]:
    """`run.log`의 `[route]`/`[degrade]`/`[failover]`/`[gate]` 줄을 RECORDED_DECISION 후보로 뽑는다.
    orchestrator가 실행 중 실제로 내린 판단을 그대로 인용하는 것이라 LLM 호출이 필요 없다."""
    return [{"kind": m.group(1), "text": m.group(2).strip()}
           for m in _ROUTE_LOG_RE.finditer(log_text)]


def _acquire_claims(acquire_data: dict[str, Any]) -> list[dict[str, Any]]:
    """ACQUIRE(`acquire.json`)의 기계검증된 QA를 claim으로 변환한다.

    verdict 매핑(acquire.py의 `apply_verdicts` 의미를 그대로 존중):
    confirmed(경로+심볼 확인) → FACT · partial(경로만 확인, "위치 힌트로만") → OPEN_QUESTION ·
    dropped/contradicted(경로 자체가 없음, 폐기됨) → OPEN_QUESTION(가설이 파기됐다는 사실 자체는 남김).
    """
    out: list[dict[str, Any]] = []

    def _one(qa: dict, forced_type: str | None) -> dict[str, Any] | None:
        answer = qa.get("answer")
        answer = answer if isinstance(answer, dict) else {}
        text = str(answer.get("answer") or qa.get("claim") or "").strip()
        if not text:
            return None
        evidence = answer.get("evidence")
        evidence = evidence if isinstance(evidence, list) else []
        refs = [{"file": str(e.get("path") or ""), "symbol_or_hunk": str(e.get("symbol") or ""),
                "source": "acquire"} for e in evidence if isinstance(e, dict)]
        verdict = qa.get("verdict")
        ctype = forced_type or (FACT if verdict == "confirmed" else OPEN_QUESTION)
        return {
            "claim_id": _claim_id("acquire", str(qa.get("claim_id") or text[:40])),
            "type": ctype, "text": text[:500], "evidence_refs": refs, "symbol_hash": "",
        }

    for qa in acquire_data.get("qa_items") or []:
        if isinstance(qa, dict):
            c = _one(qa, None)
            if c:
                out.append(c)
    for qa in acquire_data.get("dropped") or []:
        if isinstance(qa, dict):
            c = _one(qa, OPEN_QUESTION)
            if c:
                c["text"] = f"[폐기된 가설] {c['text']} (근거: {qa.get('reason', '확인 안 됨')})"
                out.append(c)
    return out


def build_understanding_bundle(run_dir: Path, review_root: Path | None = None,
                               workdir: Path | None = None) -> dict[str, Any]:
    """mode=off 전체(그리고 light/standard/deep의 기반)가 쓰는 조립기. 신규 LLM 호출 0.

    입력: `run_dir`(`.yok3x/runs/<run_id>` — run.log·acquire.json이 있는 곳),
    `review_root`(F1-d 검토번들 root — changes.diff/changes.json이 있는 곳, review 모드
    미사용 런이면 None — 그 경우 diff 기반 claim만 생략되고 나머지는 정상 조립됨, 예외 없음),
    `workdir`(S3 Drift Detector용 — claim이 참조하는 파일의 현재 콘텐츠를 해시해둘 기준 경로,
    없으면 drift 추적을 생략).

    반환: {"claims": [...], "generated_at": epoch, "run_dir": str, "file_hashes": {...}}.
    파일이 없거나 깨져도 빈 부분만 건너뛴다(폴백 가드 — Cognitive Sync Layer가 런 자체를 방해하면
    안 됨).
    """
    claims: list[dict[str, Any]] = []

    diff_text = ""
    if review_root is not None:
        try:
            diff_text = (review_root / "changes.diff").read_text(encoding="utf-8")
        except OSError:
            diff_text = ""
    if diff_text.strip():
        files, symbols = _parse_diff(diff_text)
        if files:
            claims.append({
                "claim_id": _claim_id("fact", "files", *files),
                "type": FACT, "text": f"{len(files)}개 파일이 변경됨: {', '.join(files)}",
                "evidence_refs": [{"file": f, "symbol_or_hunk": "", "source": "diff"} for f in files],
                "symbol_hash": hashlib.sha1(diff_text.encode("utf-8")).hexdigest()[:16],
            })
        for fpath, syms in symbols.items():
            claims.append({
                "claim_id": _claim_id("inference", "symbols", fpath, *syms),
                "type": INFERENCE,
                "text": f"{fpath}에서 함수/클래스로 보이는 심볼이 추가·수정된 것으로 보임: "
                       f"{', '.join(syms)}",
                "evidence_refs": [{"file": fpath, "symbol_or_hunk": s, "source": "diff"} for s in syms],
                "symbol_hash": hashlib.sha1((fpath + "\0" + "\0".join(syms))
                                            .encode("utf-8")).hexdigest()[:16],
            })

    try:
        log_text = (run_dir / "run.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    for i, dec in enumerate(_parse_run_log_decisions(log_text)):
        claims.append({
            "claim_id": _claim_id("decision", str(i), dec["kind"], dec["text"]),
            "type": RECORDED_DECISION, "text": dec["text"],
            "evidence_refs": [{"file": "run.log", "symbol_or_hunk": dec["kind"], "source": "run_log"}],
            "symbol_hash": "",
        })

    try:
        acquire_data = json.loads((run_dir / "acquire.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        acquire_data = None
    if isinstance(acquire_data, dict):
        claims.extend(_acquire_claims(acquire_data))

    # 근거 없이 FACT/RECORDED_DECISION으로 분류된 claim은 강등한다(정직 표기 — 억지로 채우지 않음).
    for c in claims:
        if c["type"] in (FACT, RECORDED_DECISION) and not c["evidence_refs"]:
            c["type"] = OPEN_QUESTION

    return {"claims": claims, "generated_at": time.time(), "run_dir": str(run_dir),
           "file_hashes": _hash_referenced_files(claims, workdir)}


def _hash_referenced_files(claims: list[dict[str, Any]], workdir: Path | None) -> dict[str, str]:
    """S3(Drift Detector) 기반: claim들이 참조하는 각 파일의 **현재 시점** 콘텐츠 해시를 찍어둔다.
    나중에 `check_drift()`가 이 시점의 해시와 재조회 시점의 해시를 비교해 낡은 claim을 찾는다.

    **정직한 한계**: 파일 단위 해시다(codex 권고인 hunk·심볼 단위가 이상적이지만, 그러려면 AST
    파서가 필요해 의존성0 stdlib 범위를 벗어난다). 파일이 조금이라도 바뀌면 그 파일을 근거로 쓰는
    claim 전부가 STALE로 표시된다 — 과소 무효화(낡은 설명이 살아남음)보다 과다 무효화(불필요한
    재검증 요구)가 안전한 방향이라 이 쪽으로 보수적으로 정했다."""
    if workdir is None:
        return {}
    out: dict[str, str] = {}
    seen: set[str] = set()
    for c in claims:
        for ref in c["evidence_refs"]:
            f = ref.get("file") or ""
            if not f or f in seen or f == "run.log":
                continue
            seen.add(f)
            try:
                out[f] = hashlib.sha1((workdir / f).read_bytes()).hexdigest()
            except OSError:
                pass   # 파일이 없어졌거나 읽을 수 없음 — drift 판정 시 "확인불가"로 자연히 처리됨
    return out


def check_drift(bundle: dict[str, Any], workdir: Path) -> list[str]:
    """저장된 `file_hashes`와 현재 파일 내용을 비교해 낡아진 claim_id 목록을 낸다.

    파일이 사라졌거나 읽을 수 없어도(예: 삭제됨) STALE로 취급한다 — "확인 불가"를 "안 바뀜"으로
    오인하면 정확도 손실이 생기므로(§3.5 codex 경고: 근거 있는 환각 방지와 같은 원칙)."""
    old = bundle.get("file_hashes") or {}
    if not old:
        return []
    now: dict[str, str | None] = {}
    for f in old:
        try:
            now[f] = hashlib.sha1((workdir / f).read_bytes()).hexdigest()
        except OSError:
            now[f] = None
    stale_files = {f for f, h in old.items() if now.get(f) != h}
    if not stale_files:
        return []
    return [c["claim_id"] for c in bundle.get("claims", [])
           if any((ref.get("file") in stale_files) for ref in c["evidence_refs"])]


def bundle_cache_key(run_dir: Path, review_root: Path | None, mode: str) -> str:
    """§3.4 캐시 키: base/proposed/diff 해시 + ACQUIRE 답변 해시 + mode. 동일 입력이면 재조립하지
    않는다(off 모드는 LLM 비용이 없어 저비용이지만, light 이상의 캐시 키로도 그대로 재사용된다)."""
    parts = [mode]
    if review_root is not None:
        try:
            changes = json.loads((review_root / "changes.json").read_text(encoding="utf-8"))
            for f in changes.get("files") or []:
                parts.append(str(f.get("base_sha256") or ""))
                parts.append(str(f.get("proposed_sha256") or ""))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        parts.append(hashlib.sha1((run_dir / "acquire.json").read_bytes()).hexdigest())
    except OSError:
        pass
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:16]


# S4: T1(triage.estimate_execution) tier별 정적 comprehension checklist. LLM 호출 없음 — 위험도에
# 맞는 템플릿을 고를 뿐이다(codex 권고: 정적 checklist, LLM 판정 아님). tier는 triage.py의 실제
# 값(direct|local|api, "규모≠위험"으로 이미 종합판정된 결과)을 그대로 쓴다.
_CHECKLIST_BY_TIER: dict[str, list[str]] = {
    "direct": [
        "위 FACT(변경 파일 목록)가 예상과 일치합니까?",
    ],
    "local": [
        "위 FACT/RECORDED_DECISION의 변경 파일·심볼이 예상과 일치합니까?",
        "OPEN_QUESTION으로 남은 항목 중 지금 결정을 미뤄도 되는 게 맞습니까?",
    ],
    "api": [
        "이 변경이 실패하면 어떤 하위 시스템·사용자에게 영향이 갑니까?",
        "위 RECORDED_DECISION 중 되돌리기 어려운 결정이 있다면, 대안을 알고 있습니까?",
        "OPEN_QUESTION 항목이 실제로 이 변경의 위험과 무관하다고 확신할 근거가 있습니까?",
        "테스트 통과가 '의도한 동작을 검증'입니까, 아니면 '우연히 안 깨짐'에 가깝습니까?",
    ],
}


def static_checklist(tier: str) -> list[str]:
    """tier(triage.py의 direct|local|api)에 맞는 정적 이해 체크리스트. 모르는 tier는 가장 엄격한
    'api'로 fail-closed(위험을 과소평가하는 쪽보다 과대평가하는 쪽이 안전)."""
    return list(_CHECKLIST_BY_TIER.get(tier, _CHECKLIST_BY_TIER["api"]))


def render_markdown(bundle: dict[str, Any], tier: str | None = None) -> str:
    """review bundle에 함께 게시할 사람이 읽는 요약(S5). 주장 단위로 근거를 병기하고,
    `tier`가 주어지면 위험도에 맞는 정적 체크리스트(S4)도 붙인다."""
    lines = ["# 변경 이해 요약(Cognitive Sync Layer, 기계 조립 — LLM 호출 0)", ""]
    by_type = {t: [c for c in bundle["claims"] if c["type"] == t] for t in CLAIM_TYPES}
    labels = {FACT: "확인된 사실", RECORDED_DECISION: "기록된 결정",
             INFERENCE: "추정(확정 아님)", OPEN_QUESTION: "미해결/근거부족"}
    for t in CLAIM_TYPES:
        items = by_type[t]
        if not items:
            continue
        lines.append(f"## {labels[t]} ({t})")
        for c in items:
            lines.append(f"- {c['text']}")
            for ref in c["evidence_refs"]:
                loc = ref["file"] + (f"::{ref['symbol_or_hunk']}" if ref["symbol_or_hunk"] else "")
                lines.append(f"  - 근거: {loc} ({ref['source']})")
        lines.append("")
    if not bundle["claims"]:
        lines.append("(조립할 근거 없음 — review 번들·run.log·ACQUIRE 데이터가 이번 런에 없었음)")
    if tier:
        lines.append(f"## 이해 체크리스트 (위험도: {tier})")
        for q in static_checklist(tier):
            lines.append(f"- [ ] {q}")
        lines.append("")
    return "\n".join(lines)
