# V-1/V-8/T-6 관측 로그 집계(읽기 전용, YOK-121 안 B).
#
# 왜: runbooks.jsonl·calibration.jsonl·review_protocol_observations.jsonl은 각 작업의
# `.yok3x/`(config root 기준)에 따로 쌓인다. min_samples(기본 5) 표본이 한 폴더에 안 모이면
# 힌트가 영원히 안 나온다 — 그런데 다른 폴더에는 이미 쌓여 있을 수 있다.
# 이 모듈은 여러 루트 아래의 `.yok3x/*.jsonl`을 **읽기만** 해서 합쳐 보여준다. 저장 구조는
# 안 바꾼다(그대로 두면 A안, 이 집계가 B안). C안(전역 저장소로 실제 합치기)은 여기서 하지 않는다.
#
# 계획서: docs/plans/v4.x-plan-observation-store-scope-2026-09-21.md
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import calibration

_KNOWN_FILES = ("runbooks.jsonl", "calibration.jsonl", "review_protocol_observations.jsonl")


def find_data_files(roots: list[str | Path]) -> dict[str, list[Path]]:
    """주어진 루트들 아래 `.yok3x/<known>.jsonl`을 모두 찾는다.

    권한 거부·존재하지 않는 경로는 조용히 건너뛴다(이 폴더가 없거나 못 읽는다고 전체를
    실패시키지 않음 — 다른 세션의 pytest 임시 폴더가 실제로 이런 사례였다).
    """
    found: dict[str, list[Path]] = {name: [] for name in _KNOWN_FILES}
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        try:
            for p in root.rglob(".yok3x"):
                if not p.is_dir():
                    continue
                for name in _KNOWN_FILES:
                    f = p / name
                    if f.is_file():
                        found[name].append(f)
        except OSError:
            continue
    return found


def _read_jsonl(path: Path) -> tuple[list[dict], int]:
    """줄 단위 JSON 파싱. (레코드 목록, 깨진 줄 수)를 돌려준다."""
    records: list[dict] = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return records, 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            malformed += 1
            continue
        if isinstance(rec, dict):
            records.append(rec)
        else:
            malformed += 1
    return records, malformed


def aggregate_observations(roots: list[str | Path], *, min_samples: int = 5) -> dict[str, Any]:
    """읽기 전용 집계. 파일을 옮기거나 고치지 않는다.

    반환:
      files: 종류별로 찾은 파일 수
      runbooks: {total, malformed, by_bucket_pattern: [{bucket, pattern, count, tags, min_samples_met}]}
      calibration: {total, malformed, summary(calibration.summarize 결과)}
      review_protocol: {total, malformed, by_reviewer: {reviewer: count}}
    """
    files = find_data_files(roots)

    rb_records: list[dict] = []
    rb_malformed = 0
    for f in files["runbooks.jsonl"]:
        recs, bad = _read_jsonl(f)
        rb_records.extend(recs)
        rb_malformed += bad
    rb_groups: dict[tuple[Any, Any], list[dict]] = {}
    for rec in rb_records:
        key = (rec.get("bucket"), rec.get("pattern"))
        rb_groups.setdefault(key, []).append(rec)
    rb_by_bp = []
    for (bucket, pattern), recs in sorted(rb_groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        tags = Counter()
        for r in recs:
            for t in r.get("approach_tags") or []:
                tags[t] += 1
        rb_by_bp.append({
            "bucket": bucket, "pattern": pattern, "count": len(recs),
            "tags": dict(tags), "min_samples_met": len(recs) >= min_samples,
        })

    calib_records: list[dict] = []
    calib_malformed = 0
    for f in files["calibration.jsonl"]:
        loaded = calibration.read_calibration_jsonl(f, window=10**9)
        calib_records.extend(loaded["records"])
        calib_malformed += sum(loaded["reasons"].values())
    calib_summary = calibration.summarize(calib_records) if calib_records else None

    rp_records: list[dict] = []
    rp_malformed = 0
    for f in files["review_protocol_observations.jsonl"]:
        recs, bad = _read_jsonl(f)
        rp_records.extend(recs)
        rp_malformed += bad
    rp_by_reviewer = Counter(str(r.get("reviewer")) for r in rp_records)

    return {
        "roots": [str(Path(r)) for r in roots],
        "files": {name: len(paths) for name, paths in files.items()},
        "runbooks": {
            "total": len(rb_records), "malformed": rb_malformed, "by_bucket_pattern": rb_by_bp,
        },
        "calibration": {
            "total": len(calib_records), "malformed": calib_malformed, "summary": calib_summary,
        },
        "review_protocol": {
            "total": len(rp_records), "malformed": rp_malformed,
            "by_reviewer": dict(rp_by_reviewer),
        },
        "min_samples": min_samples,
    }
