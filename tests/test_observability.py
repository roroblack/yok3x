import json
from pathlib import Path

from yok3x.observability import aggregate_observations, find_data_files


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_find_data_files_recurses_and_skips_missing(tmp_path):
    proj_a = tmp_path / "proj_a" / ".yok3x"
    proj_b = tmp_path / "sub" / "proj_b" / ".yok3x"
    _write_jsonl(proj_a / "runbooks.jsonl", [{"bucket": "small", "pattern": "x"}])
    _write_jsonl(proj_b / "calibration.jsonl", [{"score": 9, "verify_ok": True}])
    # tmp_path/missing does not exist — must not raise.
    found = find_data_files([tmp_path, tmp_path / "missing"])
    assert len(found["runbooks.jsonl"]) == 1
    assert len(found["calibration.jsonl"]) == 1
    assert found["review_protocol_observations.jsonl"] == []


def test_aggregate_merges_runbooks_across_roots_by_bucket_pattern(tmp_path):
    proj_a = tmp_path / "proj_a" / ".yok3x"
    proj_b = tmp_path / "proj_b" / ".yok3x"
    _write_jsonl(proj_a / "runbooks.jsonl", [
        {"bucket": "small", "pattern": "producer-reviewer", "approach_tags": ["tdd-first"]},
    ])
    _write_jsonl(proj_b / "runbooks.jsonl", [
        {"bucket": "small", "pattern": "producer-reviewer", "approach_tags": ["tdd-first"]},
        {"bucket": "small", "pattern": "producer-reviewer", "approach_tags": ["simple-direct"]},
    ])
    agg = aggregate_observations([tmp_path], min_samples=3)
    rb = agg["runbooks"]
    assert rb["total"] == 3
    assert len(rb["by_bucket_pattern"]) == 1
    row = rb["by_bucket_pattern"][0]
    assert row["bucket"] == "small" and row["pattern"] == "producer-reviewer"
    assert row["count"] == 3
    assert row["tags"] == {"tdd-first": 2, "simple-direct": 1}
    assert row["min_samples_met"] is True  # 3 records >= min_samples=3


def test_aggregate_min_samples_not_met_when_scattered_below_threshold(tmp_path):
    proj_a = tmp_path / "proj_a" / ".yok3x"
    _write_jsonl(proj_a / "runbooks.jsonl", [
        {"bucket": "small", "pattern": "producer-reviewer", "approach_tags": ["tdd-first"]},
    ])
    agg = aggregate_observations([tmp_path], min_samples=5)
    row = agg["runbooks"]["by_bucket_pattern"][0]
    assert row["min_samples_met"] is False


def test_aggregate_skips_malformed_lines_without_raising(tmp_path):
    proj = tmp_path / "proj" / ".yok3x"
    proj.mkdir(parents=True)
    (proj / "runbooks.jsonl").write_text(
        '{"bucket": "small", "pattern": "x"}\nnot json\n{"bucket": "small", "pattern": "x"}\n',
        encoding="utf-8",
    )
    agg = aggregate_observations([tmp_path])
    assert agg["runbooks"]["total"] == 2
    assert agg["runbooks"]["malformed"] == 1


def test_aggregate_calibration_and_review_protocol_counted(tmp_path):
    proj = tmp_path / "proj" / ".yok3x"
    _write_jsonl(proj / "calibration.jsonl", [
        {"score": 9.0, "verify_ok": True, "pattern": "producer-reviewer"},
        {"score": 3.0, "verify_ok": False, "pattern": "producer-reviewer"},
    ])
    _write_jsonl(proj / "review_protocol_observations.jsonl", [
        {"reviewer": "claude", "source": "structured", "parse_error": None},
        {"reviewer": "codex", "source": "structured", "parse_error": None},
    ])
    agg = aggregate_observations([tmp_path])
    assert agg["calibration"]["total"] == 2
    assert agg["calibration"]["summary"] is not None
    assert agg["review_protocol"]["total"] == 2
    assert agg["review_protocol"]["by_reviewer"] == {"claude": 1, "codex": 1}


def test_aggregate_empty_roots_returns_zero_counts_not_error(tmp_path):
    agg = aggregate_observations([tmp_path])
    assert agg["runbooks"]["total"] == 0
    assert agg["calibration"]["total"] == 0
    assert agg["calibration"]["summary"] is None
    assert agg["review_protocol"]["total"] == 0
