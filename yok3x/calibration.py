# 심판 캘리브레이션(F0). 설계 근거: CLIP 평가 교훈 + codex 공동검토 2026-07-18.
#
# 왜: 우리는 LLM 리뷰어의 SCORE를 지상진실과 한 번도 대조한 적이 없다. SCORE 8이 실제 테스트 통과
# (verify_cmd)를 예측하는지 모른다 — 상관이 없으면 producer-reviewer 게이트는 연극이다.
# 이 모듈은 (SCORE, verify 통과, 특징)을 런마다 기록하고, 쌓이면 상관·혼동행렬로 게이트를 검증한다.
#
# 규율(codex): (1) 측정 기준을 데이터 수집 '전에' 확정(스키마 고정) (2) 선택 편향 주의 — 우리는 '고른
# 경로'의 결과만 관측하므로 이 기록을 곧바로 정답으로 학습하면 편향(초기엔 관측·상관 확인용) (3) 순수
# stdlib 통계, 데이터 없이 ML 서브시스템 짓지 않음.
from __future__ import annotations

import math

# 라운드별 캘리브레이션 레코드 스키마. 후보를 검증한 verify_ok만 지상진실로 쓴다.
FIELDS = ("run_id", "ts", "pattern", "backend", "effort", "rounds",
          "score", "verify_ok", "verify_scope", "tokens", "cost_usd", "duration_ms", "issues",
          "reviewer", "threshold", "gate_pass", "gate_mode", "round")


def make_record(**kw) -> dict:
    """스키마에 맞춘 레코드 생성(누락 필드는 None). 값 타입은 로깅측이 보장."""
    unknown = set(kw) - set(FIELDS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise TypeError(f"unknown calibration field(s): {names}")
    return {f: kw.get(f) for f in FIELDS}


def _labeled(records: list[dict]) -> list[dict]:
    """상관/혼동에는 후보 자체를 검증한 유한 SCORE+bool 라벨만 쓴다."""
    out = []
    for r in records:
        s, v = r.get("score"), r.get("verify_ok")
        if (r.get("verify_scope") == "candidate"
                and isinstance(s, (int, float)) and isinstance(v, bool)
                and math.isfinite(float(s))):
            out.append(r)
    return out


def point_biserial(records: list[dict]) -> float | None:
    """SCORE(연속) ↔ verify 통과(이진)의 점이연 상관. -1~1. 표본<2 또는 분산0이면 None.
    양수↑ = SCORE가 높을수록 실제로 통과 → 게이트가 신호가 있음. ~0 = 게이트가 연극."""
    rows = _labeled(records)
    n = len(rows)
    if n < 2:
        return None
    scores = [float(r["score"]) for r in rows]
    labels = [1.0 if r["verify_ok"] else 0.0 for r in rows]
    mean_s = sum(scores) / n
    var_s = sum((x - mean_s) ** 2 for x in scores) / n
    p = sum(labels) / n                       # 통과 비율
    if var_s <= 0 or p <= 0 or p >= 1:        # SCORE 분산0 or 라벨이 한쪽뿐이면 정의 안 됨
        return None
    std_s = math.sqrt(var_s)
    m1 = sum(s for s, l in zip(scores, labels) if l == 1.0) / (p * n)      # 통과군 평균 SCORE
    m0 = sum(s for s, l in zip(scores, labels) if l == 0.0) / ((1 - p) * n)  # 실패군 평균 SCORE
    return (m1 - m0) / std_s * math.sqrt(p * (1 - p))


def confusion_at(records: list[dict], threshold: float) -> dict:
    """'SCORE>=threshold가 통과를 예측한다'의 혼동행렬 + 지표. label 있는 레코드만."""
    rows = _labeled(records)
    tp = fp = tn = fn = 0
    for r in rows:
        pred = float(r["score"]) >= threshold          # 게이트가 통과시킬 것
        actual = bool(r["verify_ok"])                  # 실제 테스트 통과
        if pred and actual: tp += 1
        elif pred and not actual: fp += 1              # 게이트는 통과시켰는데 실제론 실패(위험!)
        elif not pred and actual: fn += 1              # 게이트가 막았는데 실제론 통과(과엄격)
        else: tn += 1
    prec = tp / (tp + fp) if (tp + fp) else None       # 게이트 통과분 중 실제 통과율
    rec = tp / (tp + fn) if (tp + fn) else None
    return {"threshold": threshold, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "n": tp + fp + tn + fn, "precision": prec, "recall": rec}


def summarize(records: list[dict], threshold: float = 8.0) -> dict:
    """전체 요약. n_total(특징만 있는 것 포함)·n_labeled(지상진실 있는 것)·상관·혼동·통과율."""
    rows = _labeled(records)
    n_lab = len(rows)
    pass_rate = (sum(1 for r in rows if r["verify_ok"]) / n_lab) if n_lab else None
    corr = point_biserial(records)
    return {
        "n_total": len(records), "n_labeled": n_lab, "pass_rate": pass_rate,
        "score_verify_corr": corr, "confusion": confusion_at(records, threshold),
        # 해석 힌트: |corr|이 작고(≈0) precision이 통과율과 비슷하면 게이트가 신호를 못 준다는 뜻.
        "verdict": ("표본 부족" if n_lab < 10 else
                    "게이트 무의미 의심(상관 낮음)" if (corr is None or abs(corr) < 0.2) else
                    "게이트 신호 있음"),
    }
