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
import json
from collections import Counter
from collections import deque
from pathlib import Path
from statistics import mean, median

# 라운드별 캘리브레이션 레코드 스키마. 후보를 검증한 verify_ok만 지상진실로 쓴다.
FIELDS = ("run_id", "ts", "pattern", "backend", "effort", "rounds",
          "score", "verify_ok", "verify_scope", "tokens", "cost_usd", "duration_ms", "issues",
          "reviewer", "threshold", "gate_pass", "gate_mode", "round", "bucket")


_MIN_CALIBRATION_SAMPLE = 3


def _valid_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def read_calibration_jsonl(path: str | Path, window: int = 20) -> dict:
    """Read usable calibration summaries without exposing or persisting task text.

    Invalid lines are isolated and reported in ``reasons``.  The window is
    applied to usable records in file order, so malformed lines do not consume
    the caller's calibration sample budget.
    """
    reasons = Counter()
    file_path = Path(path)
    if not file_path.is_file():
        reasons["file_not_found"] += 1
        return {
            "records": [], "reasons": dict(reasons), "window": max(0, window),
            "bucket_info": "bucket 정보 없음", "model_info": "model 정보 없음",
        }

    usable_count = 0
    usable = deque(maxlen=max(0, window) if isinstance(window, int) and not isinstance(window, bool) else 20)
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        reasons["file_unreadable"] += 1
        return {
            "records": [], "reasons": dict(reasons), "window": max(0, window),
            "bucket_info": "bucket 정보 없음", "model_info": "model 정보 없음",
        }
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            reasons["malformed_json"] += 1
            continue
        if not isinstance(record, dict):
            reasons["schema_mismatch"] += 1
            continue
        if "score" not in record:
            reasons["missing_score"] += 1
            continue
        if "verify_ok" not in record:
            reasons["missing_verify_ok"] += 1
            continue
        if not _valid_number(record["score"]):
            reasons["schema_mismatch"] += 1
            continue
        if record["verify_ok"] is not None and not isinstance(record["verify_ok"], bool):
            reasons["schema_mismatch"] += 1
            continue
        for field in ("pattern", "backend", "effort", "reviewer", "bucket"):
            if field in record and record[field] is not None and not isinstance(record[field], str):
                reasons["schema_mismatch"] += 1
                break
        else:
            if "rounds" in record and record["rounds"] is not None and not _valid_number(record["rounds"]):
                reasons["schema_mismatch"] += 1
            else:
                # Keep only aggregate inputs.  In particular, never return
                # arbitrary/unknown fields that might contain task or prompt text.
                usable_count += 1
                usable.append({field: record[field] for field in
                               ("pattern", "backend", "effort", "reviewer", "bucket",
                                "score", "verify_ok", "rounds") if field in record})

    limit = max(0, int(window)) if isinstance(window, int) and not isinstance(window, bool) else 20
    records = list(usable) if limit else []
    if usable_count > len(records):
        reasons["outside_window"] += usable_count - len(records)
    if records and any("bucket" not in record or record.get("bucket") is None for record in records):
        reasons["bucket_info_missing"] += 1
    bucket_info = "bucket 정보 없음" if any("bucket" not in r or r.get("bucket") is None for r in records) else "bucket 정보 있음"
    model_info = "model 정보 없음" if any("model" not in r or r.get("model") is None for r in records) else "model 정보 있음"
    return {"records": records, "reasons": dict(reasons), "window": limit,
            "bucket_info": bucket_info, "model_info": model_info}


def aggregate_calibration_statistics(records: list[dict], reasons: dict | None = None) -> dict:
    """Aggregate calibration records by the fields actually available in S3."""
    groups = {}
    result_reasons = Counter(reasons or {})
    for record in records or []:
        if not isinstance(record, dict) or not _valid_number(record.get("score")):
            result_reasons["schema_mismatch"] += 1
            continue
        if record.get("verify_ok") is not None and not isinstance(record.get("verify_ok"), bool):
            result_reasons["schema_mismatch"] += 1
            continue
        key = tuple(record.get(field) for field in ("pattern", "backend", "effort", "reviewer"))
        groups.setdefault(key, []).append(record)
    output = []
    for key, rows in sorted(groups.items(), key=lambda item: tuple(str(v or "") for v in item[0])):
        scores = [float(row["score"]) for row in rows]
        verified = [row["verify_ok"] for row in rows if isinstance(row.get("verify_ok"), bool)]
        rounds = [float(row["rounds"]) for row in rows if _valid_number(row.get("rounds"))]
        n = len(rows)
        confidence = min(1.0, n / _MIN_CALIBRATION_SAMPLE)
        group = {
            "pattern": key[0], "backend": key[1], "effort": key[2], "reviewer": key[3],
            "bucket": next((row.get("bucket") for row in rows if row.get("bucket") is not None), None),
            "sample_count": n, "moving_average_score": mean(scores), "median_score": median(scores),
            "success_rate": (sum(verified) / len(verified)) if verified else None,
            "average_rounds": mean(rounds) if rounds else None,
            "confidence": confidence,
            "confidence_reason": "표본 부족" if n < _MIN_CALIBRATION_SAMPLE else "표본 충분",
        }
        output.append(group)
    has_missing_bucket = any(group["bucket"] is None for group in output)
    if has_missing_bucket:
        result_reasons["bucket_info_missing"] += 1 if "bucket_info_missing" not in result_reasons else 0
    return {"groups": output, "reasons": dict(result_reasons),
            "bucket_info": "bucket 정보 없음" if has_missing_bucket else "bucket 정보 있음",
            "model_info": "model 정보 없음"}


def load_calibration_statistics(path: str | Path, window: int = 20) -> dict:
    """Read a calibration JSONL file and return deterministic S3 statistics."""
    loaded = read_calibration_jsonl(path, window=window)
    aggregated = aggregate_calibration_statistics(loaded["records"], loaded["reasons"])
    return {**loaded, **aggregated}


def rounds_by_bucket(records: list[dict], *, min_samples: int = _MIN_CALIBRATION_SAMPLE) -> dict:
    """Aggregate rounds-to-pass by (bucket, pattern) from real calibration history.

    Evidence-based counterpart to the static text-heuristic bucket→rounds lookup in
    ``automation.recommend_effort_rounds`` — this reads what actually happened instead of
    guessing from task length. Advisory only: callers decide whether to surface or act on
    it (see docs/plans/v4.x-plan-rounds-calibration-hint-2026-08-30.md). A group with fewer
    than ``min_samples`` observations is marked unavailable rather than guessed at (same
    fail-closed convention as ``calibrated_benchmark_scores``).
    """
    threshold = min_samples if isinstance(min_samples, int) and not isinstance(min_samples, bool) else _MIN_CALIBRATION_SAMPLE
    threshold = max(0, threshold)
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        bucket, pattern = record.get("bucket"), record.get("pattern")
        if not isinstance(bucket, str) or not bucket or not isinstance(pattern, str) or not pattern:
            continue
        if record.get("verify_ok") is not None and not isinstance(record.get("verify_ok"), bool):
            continue
        groups.setdefault((bucket, pattern), []).append(record)

    output = []
    for (bucket, pattern), rows in sorted(groups.items()):
        rounds = [float(r["rounds"]) for r in rows if _valid_number(r.get("rounds"))]
        verified = [r["verify_ok"] for r in rows if isinstance(r.get("verify_ok"), bool)]
        n = len(rows)
        entry = {"bucket": bucket, "pattern": pattern, "sample_count": n,
                 "available": n >= threshold and bool(rounds)}
        if entry["available"]:
            entry["median_rounds"] = median(rounds)
            entry["mean_rounds"] = mean(rounds)
            entry["success_rate"] = (sum(verified) / len(verified)) if verified else None
        else:
            entry["reason"] = (f"samples below min_samples={threshold}" if n < threshold
                                else "no rounds data in samples")
        output.append(entry)
    return {"groups": output}


def rounds_hint_for(records: list[dict], *, bucket: str, pattern: str,
                    min_samples: int = _MIN_CALIBRATION_SAMPLE) -> dict:
    """Convenience wrapper: the single (bucket, pattern) entry from ``rounds_by_bucket``."""
    for entry in rounds_by_bucket(records, min_samples=min_samples)["groups"]:
        if entry["bucket"] == bucket and entry["pattern"] == pattern:
            return entry
    threshold = min_samples if isinstance(min_samples, int) and not isinstance(min_samples, bool) else _MIN_CALIBRATION_SAMPLE
    return {"bucket": bucket, "pattern": pattern, "sample_count": 0, "available": False,
            "reason": f"samples below min_samples={max(0, threshold)}"}


# Alternate descriptive names for callers integrating the S3 seam.
read_calibration_stats = load_calibration_statistics


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
        # corr=None은 "상관이 낮다"가 아니라 **계산 불가**다(라벨이 한쪽뿐이라 분산 0 등).
        # 둘을 같은 문구로 뭉개면 "게이트가 무의미하다"는 결론을 데이터 없이 주장하게 된다
        # (실측: T-2 1차 표본이 verify_ok 전부 True라 corr=None인데 '상관 낮음'으로 표시됨).
        "verdict": ("표본 부족" if n_lab < 10 else
                    "판정 불가(라벨이 한쪽뿐 — 상관 정의 안 됨)" if corr is None else
                    "게이트 무의미 의심(상관 낮음)" if abs(corr) < 0.2 else
                    "게이트 신호 있음"),
    }
