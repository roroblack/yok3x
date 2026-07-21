# 자동 트리아지 T1 — 착수 전 실행 '형태' 추천(순수 규칙, 의존성0, 호출0).
#
# 설계(reports/v4.x-assessment-auto-triage-2026-07-18): **규모≠위험**. 단일 등급으로 티어를
# 정하지 않고 4축을 분리한다 — 복잡도·실패영향도·검증가능성·비용. escalate(실제 실패신호)는
# 유지하고, 트리아지는 그 앞의 '저비용 시작점'만 보수적으로 고른다.
#
# **추천 전용**: 이 함수는 판정만 반환하고 자동 적용하지 않는다(오케스트레이터가 로그·표시만).
# 자동화 우선순위(위험 낮은 순): 패턴 추천 > 라운드수(상한 1~2) > 워커 티어 > 검토 생략.
# **검토 생략이 가장 위험** — 작고 되돌릴 수 있고 결정적 검증(verify_cmd)을 통과하는 작업만 후보.
from __future__ import annotations

from typing import Any

_LEVELS = {"low": 0, "mid": 1, "high": 2}


def _spec_str(spec: dict[str, Any], key: str) -> str:
    v = spec.get(key)
    return str(v).strip() if v is not None else ""


def _axes(spec: dict[str, Any]) -> dict[str, str]:
    """4축을 spec 특징만으로 평가한다(LLM 없음). 입력 텍스트는 데이터로만 쓴다."""
    task = _spec_str(spec, "task")
    globs = spec.get("context_globs") or []
    length = len(task)

    # 복잡도: 태스크 길이 + 참조 파일 수(범위). 짧아도 어려울 수 있으나 규칙은 보수적 시작점만.
    complexity = "low" if length < 200 else "mid" if length < 800 else "high"
    if isinstance(globs, (list, tuple)) and len(globs) >= 5 and complexity == "low":
        complexity = "mid"

    # 실패 영향도: 외부 쓰기 여부가 핵심(되돌리기 어려움). workdir+게시/수정 = 높음.
    writes_files = bool(spec.get("materialize")) or ("changes" in spec)
    has_workdir = bool(spec.get("workdir"))
    if writes_files and has_workdir:
        impact = "high"
    elif has_workdir or writes_files:
        impact = "mid"
    else:
        impact = "low"

    # 검증 가능성: verify_cmd로 객관 확인되면 높음.
    verifiability = "high" if _spec_str(spec, "verify_cmd") else "low"

    # 비용: 예상 호출 수(패턴×라운드). 실제 토큰은 모르니 호출 수로 근사.
    pattern = spec.get("pattern", "producer-reviewer")
    rounds = int(spec.get("max_rounds", 2) or 2)
    if pattern == "producer-reviewer":
        est_calls = 2 * max(1, rounds)
    elif pattern in ("fanout", "fanout-fanin"):
        est_calls = len(spec.get("workers") or []) + 1
    elif pattern == "pipeline":
        est_calls = len(spec.get("stages") or [])
    else:
        est_calls = 1
    cost = "low" if est_calls <= 2 else "mid" if est_calls <= 5 else "high"

    return {"complexity": complexity, "impact": impact,
            "verifiability": verifiability, "cost": cost}


def estimate_execution(spec: dict[str, Any]) -> dict[str, Any]:
    """spec만 보고 실행 형태를 추천한다. 반환:
    {pattern, tier(direct|local|api), max_rounds, skip_review, confidence, axes, reasons}.
    **추천 전용** — 호출자는 로그·표시만 하고 자동 적용하지 않는다."""
    axes = _axes(spec)
    c, impact = axes["complexity"], axes["impact"]
    verifiable = axes["verifiability"] == "high"
    reasons: list[str] = [
        f"복잡도={c}", f"실패영향도={impact}",
        f"검증가능={'예' if verifiable else '아니오'}", f"비용={axes['cost']}"]

    # 티어(direct|local|api): 복잡도·영향도의 큰 쪽으로. 영향도 높으면 규모와 무관하게 신중.
    sev = max(_LEVELS[c], _LEVELS[impact])
    tier = ("direct", "local", "api")[sev]
    if impact == "high" and tier != "api":
        tier = "api"
        reasons.append("영향도 높음 → 규모와 무관하게 신중(api 추천)")

    # 패턴: 저복잡도·저영향이면 단일(direct)로 시작, 아니면 producer-reviewer(검토 포함).
    if c == "low" and impact == "low":
        pattern = "direct"
        reasons.append("저복잡도·저영향 → 단일 실행으로 시작(escalate가 필요시 승격)")
    else:
        pattern = "producer-reviewer"

    # 라운드 수: 보수적 상한 1~2(escalate가 실제 신호로 올린다).
    max_rounds = 1 if c == "low" else 2

    # 검토 생략: 가장 위험 — 저영향 AND 검증가능 AND 저복잡도만 후보. 그 외 절대 생략 추천 안 함.
    skip_review = (impact == "low" and verifiable and c == "low")
    if skip_review:
        reasons.append("저영향·검증가능·저복잡도 → 검토 생략 후보(verify가 게이트)")
    elif not verifiable:
        reasons.append("verify_cmd 없음 → 검토 생략 불가(객관 신호 없음)")

    # 신뢰도: 축이 한 방향으로 몰릴수록 높음. 복잡도와 영향도가 엇갈리면(규모≠위험) 낮춘다.
    conflict = abs(_LEVELS[c] - _LEVELS[impact])
    confidence = round(max(0.3, 0.9 - 0.25 * conflict), 2)
    if conflict >= 2:
        reasons.append("복잡도와 영향도가 크게 엇갈림(규모≠위험) → 신뢰도 낮음, 사람 판단 권장")

    return {
        "pattern": pattern, "tier": tier, "max_rounds": max_rounds,
        "skip_review": skip_review, "confidence": confidence,
        "axes": axes, "reasons": reasons,
    }
