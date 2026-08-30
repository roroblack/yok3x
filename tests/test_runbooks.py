import json
from types import SimpleNamespace

import pytest

from yok3x.runbooks import (
    APPROACH_TAGS,
    build_hint_text,
    extract_approach_tags,
    log_runbook_entry,
    query_hint,
)


def _cfg(tmp_path):
    runs_dir = tmp_path / ".yok3x" / "runs"
    runs_dir.mkdir(parents=True)
    return SimpleNamespace(paths=SimpleNamespace(runs=runs_dir))


def test_extract_approach_tags_reads_known_vocabulary_only():
    text = "SELF-CHECK: ok\nAPPROACH_TAGS: tdd-first, incremental-refactor"
    assert extract_approach_tags(text) == ["tdd-first", "incremental-refactor"]


def test_extract_approach_tags_drops_out_of_vocabulary_words():
    text = "APPROACH_TAGS: tdd-first, my-secret-project-detail, made-up-tag"
    assert extract_approach_tags(text) == ["tdd-first"]


def test_extract_approach_tags_no_line_returns_empty():
    assert extract_approach_tags("just some code, no tag line") == []
    assert extract_approach_tags("") == []
    assert extract_approach_tags(None) == []


def test_log_runbook_entry_skips_when_not_verified_or_not_passed(tmp_path):
    cfg = _cfg(tmp_path)
    log_runbook_entry(cfg, run_id="r1", bucket="medium", pattern="producer-reviewer",
                      approach_tags=["tdd-first"], verify_ok=False, gate_passed=True,
                      score=9.0, rounds=1)
    log_runbook_entry(cfg, run_id="r1", bucket="medium", pattern="producer-reviewer",
                      approach_tags=["tdd-first"], verify_ok=True, gate_passed=False,
                      score=9.0, rounds=1)
    path = cfg.paths.runs.parent / "runbooks.jsonl"
    assert not path.exists()


def test_log_runbook_entry_skips_when_no_valid_tags(tmp_path):
    cfg = _cfg(tmp_path)
    log_runbook_entry(cfg, run_id="r1", bucket="medium", pattern="producer-reviewer",
                      approach_tags=[], verify_ok=True, gate_passed=True, score=9.0, rounds=1)
    log_runbook_entry(cfg, run_id="r1", bucket="medium", pattern="producer-reviewer",
                      approach_tags=["not-a-real-tag"], verify_ok=True, gate_passed=True,
                      score=9.0, rounds=1)
    path = cfg.paths.runs.parent / "runbooks.jsonl"
    assert not path.exists()


def test_log_runbook_entry_writes_verified_record(tmp_path):
    cfg = _cfg(tmp_path)
    log_runbook_entry(cfg, run_id="r1", bucket="medium", pattern="producer-reviewer",
                      approach_tags=["tdd-first", "not-a-real-tag"], verify_ok=True,
                      gate_passed=True, score=9.0, rounds=2)
    path = cfg.paths.runs.parent / "runbooks.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["bucket"] == "medium"
    assert rec["approach_tags"] == ["tdd-first"]
    assert rec["outcome"] == {"verify_ok": True, "gate_passed": True, "score": 9.0, "rounds": 2}


def test_query_hint_insufficient_samples_returns_unavailable(tmp_path):
    cfg = _cfg(tmp_path)
    for _ in range(3):
        log_runbook_entry(cfg, run_id="r", bucket="medium", pattern="producer-reviewer",
                          approach_tags=["tdd-first"], verify_ok=True, gate_passed=True,
                          score=9.0, rounds=1)
    hint = query_hint(cfg, bucket="medium", pattern="producer-reviewer", min_samples=5)
    assert hint["available"] is False
    assert hint["sample_count"] == 3


def test_query_hint_returns_most_common_tags_once_threshold_met(tmp_path):
    cfg = _cfg(tmp_path)
    for _ in range(4):
        log_runbook_entry(cfg, run_id="r", bucket="medium", pattern="producer-reviewer",
                          approach_tags=["tdd-first"], verify_ok=True, gate_passed=True,
                          score=9.0, rounds=1)
    log_runbook_entry(cfg, run_id="r", bucket="medium", pattern="producer-reviewer",
                      approach_tags=["simple-direct"], verify_ok=True, gate_passed=True,
                      score=9.0, rounds=1)
    hint = query_hint(cfg, bucket="medium", pattern="producer-reviewer", min_samples=5)
    assert hint["available"] is True
    assert hint["sample_count"] == 5
    assert hint["tags"][0] == "tdd-first"  # most frequent first


def test_query_hint_only_matches_same_bucket_and_pattern(tmp_path):
    cfg = _cfg(tmp_path)
    for _ in range(5):
        log_runbook_entry(cfg, run_id="r", bucket="large", pattern="producer-reviewer",
                          approach_tags=["tdd-first"], verify_ok=True, gate_passed=True,
                          score=9.0, rounds=1)
    hint = query_hint(cfg, bucket="medium", pattern="producer-reviewer", min_samples=5)
    assert hint["available"] is False
    assert hint["sample_count"] == 0


def test_build_hint_text_empty_when_unavailable():
    assert build_hint_text({"available": False}) == ""


def test_build_hint_text_is_advisory_and_does_not_inflate_with_sample_count():
    small = build_hint_text({"available": True, "tags": ["tdd-first"], "sample_count": 5})
    large = build_hint_text({"available": True, "tags": ["tdd-first"], "sample_count": 500})
    assert "지시 아님" in small and "지시 아님" in large
    assert "강제" in small and "강제" in large
    # 표본이 커져도 확신을 부풀리는 어조 변화가 없어야 한다(숫자만 다름).
    assert small.replace("5", "500") == large
