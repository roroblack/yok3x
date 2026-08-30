import json
from types import SimpleNamespace

import pytest

from yok3x.review_protocol import (
    PROTOCOL_VERSION,
    SEVERITIES,
    canonical_defect_signature,
    compute_deterministic_score,
    extract_json_candidate,
    parse_review_response,
    log_observation,
    summarize_review_protocol_observations,
)


def response(**overrides):
    value = {
        "protocol_version": PROTOCOL_VERSION,
        "score": 8,
        "defects": [{"severity": "high", "description": "  Broken   path ", "evidence": "x", "fix": "y"}],
        "summary": "summary",
    }
    value.update(overrides)
    return json.dumps(value)


def test_constants_and_full_json_success():
    assert PROTOCOL_VERSION == "review-v1"
    assert SEVERITIES == frozenset({"critical", "high", "medium", "low"})
    result = parse_review_response(response())
    assert result["source"] == "structured"
    assert result["score"] == 8.0
    assert result["defects"][0]["evidence"] == "x"


def test_empty_defects_is_valid():
    assert parse_review_response(response(defects=[]))["defects"] == []


def test_field_order_and_whitespace_do_not_change_signature():
    first = [{"severity": "high", "description": "A  bug", "evidence": "one", "fix": "one"}]
    second = [{"fix": "different", "description": " a bug ", "severity": "HIGH".lower(), "evidence": "two"}]
    assert canonical_defect_signature(first) == canonical_defect_signature(second)


def test_different_defect_content_changes_signature():
    base = [{"severity": "high", "description": "same"}]
    assert canonical_defect_signature(base) != canonical_defect_signature([{**base[0], "severity": "low"}])
    assert canonical_defect_signature(base) != canonical_defect_signature([{**base[0], "description": "other"}])


def _defect(severity):
    return {"severity": severity, "description": "d"}


def test_deterministic_score_no_defects_is_ten():
    assert compute_deterministic_score([]) == 10.0


def test_deterministic_score_is_pure_and_reproducible():
    defects = [_defect("high"), _defect("low")]
    assert compute_deterministic_score(defects) == compute_deterministic_score(list(defects))


def test_deterministic_score_applies_weights_and_rounds_to_half_point():
    # base 10 - high(2.0) - low(0.1) = 7.9, capped at 7.0 by the "high" severity cap
    assert compute_deterministic_score([_defect("high"), _defect("low")]) == 7.0


def test_deterministic_score_never_negative():
    defects = [_defect("critical")] * 10
    assert compute_deterministic_score(defects) >= 0.0


def test_deterministic_score_severity_cap_dominates_low_defect_count():
    # a single critical caps the score at 4.0 even though the raw penalty (5.0) alone would allow 5.0
    assert compute_deterministic_score([_defect("critical")]) == 4.0
    assert compute_deterministic_score([_defect("high")]) == 7.0
    assert compute_deterministic_score([_defect("medium")]) == 9.0


def test_deterministic_score_highest_severity_present_sets_the_cap():
    # raw penalty (5.0 + 0.1 = 5.1 -> score 4.9) would allow 4.9, but the critical
    # severity cap (4.0) still binds even with an extra low-severity defect mixed in
    defects = [_defect("critical"), _defect("low")]
    assert compute_deterministic_score(defects) == 4.0


def test_deterministic_score_custom_weights_and_caps_override_defaults():
    custom_weights = {"critical": 1.0, "high": 0.0, "medium": 0.0, "low": 0.0}
    custom_caps = {"critical": 9.0, "high": 10.0, "medium": 10.0}
    assert compute_deterministic_score(
        [_defect("critical")], weights=custom_weights, caps=custom_caps) == 9.0


def test_deterministic_score_ignores_unknown_severity_and_non_dict_items():
    assert compute_deterministic_score([{"severity": "unknown", "description": "x"}, "not-a-dict"]) == 10.0


def test_fenced_and_surrounded_json_are_found():
    fenced = "Here is the review:\n```json\n" + response() + "\n```\nDone."
    assert parse_review_response(fenced)["source"] == "structured"
    assert parse_review_response("Explanation {not JSON} then " + response())["source"] == "structured"


def test_braces_inside_evidence_do_not_break_extraction():
    text = response(defects=[{"severity": "low", "description": "issue", "evidence": "dict={'a': 1}"}])
    assert parse_review_response("prefix " + text + " suffix")["source"] == "structured"


def test_invalid_defect_fields_fall_back():
    cases = [
        response(defects=[{"severity": "warning", "description": "issue"}]),
        response(defects=[{"severity": "high"}]),
        response(defects=[{"severity": "high", "description": "   "}]),
        response(defects=[{"severity": "high", "description": "issue", "fix": 1}]),
        response(score=11),
    ]
    for text in cases:
        result = parse_review_response(text)
        assert result["source"] == "legacy_text"
        assert result["defects"] is None
        assert result["parse_error"]


def test_malformed_truncated_and_plain_text_fall_back_deterministically():
    cases = ['{"protocol_version":"review-v1","defects":[', '{"protocol_version":"review-v1"', "nothing to parse"]
    for text in cases:
        first = parse_review_response(text)
        second = parse_review_response(text)
        assert first == second
        assert first["source"] == "legacy_text"
        assert first["raw_text"] == text


def test_non_object_top_level_falls_back():
    assert parse_review_response("[1, 2, 3]")["source"] == "legacy_text"


def test_extract_returns_none_without_an_object():
    assert extract_json_candidate("plain text without braces") is None


@pytest.mark.parametrize("text", [None, "", "   ", "{" * 5000])
def test_extract_json_candidate_is_safe_for_empty_or_pathological_text(text):
    assert extract_json_candidate(text) is None


def test_parse_review_response_non_string_falls_back_without_raising():
    result = parse_review_response(None)
    assert result["source"] == "legacy_text"
    assert result["raw_text"] is None


def test_canonical_signature_is_stable_for_duplicate_defects_and_long_text():
    defect = {"severity": "high", "description": ("  repeated   defect  " * 20_000)}
    signature = canonical_defect_signature([defect, dict(defect)])
    assert len(signature) == 2
    assert signature[0] == signature[1]


def test_canonical_signature_tolerates_malformed_description_values():
    assert canonical_defect_signature([
        {"severity": "low", "description": None},
        {"severity": "low", "description": 123},
        "not a defect",
    ]) == ("low:", "low:123")


def test_observation_logging_swallows_write_failures(tmp_path, monkeypatch):
    cfg = observation_cfg(tmp_path)

    def fail_open(*args, **kwargs):
        raise OSError("simulated disk-full/lock")

    monkeypatch.setattr("pathlib.Path.open", fail_open)
    log_observation(cfg, run_id="r", reviewer="codex", source="structured")


def observation_cfg(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(runs=tmp_path / "runs"))


def test_observation_log_is_minimal_and_append_only(tmp_path):
    cfg = observation_cfg(tmp_path)
    log_observation(cfg, run_id="run-1", reviewer="codex", source="structured",
                    parse_error=None)
    log_observation(cfg, run_id="run-2", reviewer="claude", source="legacy_text",
                    parse_error="invalid JSON; defect description must not leak")
    path = tmp_path / "review_protocol_observations.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run-1"
    assert rows[1]["source"] == "legacy_text"
    assert set(rows[0]) == {"ts", "run_id", "reviewer", "source", "parse_error"}
    assert "raw_text" not in rows[1]
    assert "defect description" in rows[1]["parse_error"]


def test_observation_summary_insufficient_and_empty_file_safe(tmp_path):
    cfg = observation_cfg(tmp_path)
    assert summarize_review_protocol_observations(cfg)["status"] == "insufficient_data"
    log_observation(cfg, run_id="r", reviewer="codex", source="structured")
    summary = summarize_review_protocol_observations(cfg)
    assert summary["total"] == 1
    assert summary["structured_ratio"] == 1.0
    assert summary["reviewers"]["codex"]["structured"] == 1


def test_observation_summary_status_window_errors_and_reviewer_split(tmp_path):
    cfg = observation_cfg(tmp_path)
    for index in range(6):
        log_observation(
            cfg, run_id=f"r-{index}", reviewer="codex" if index < 4 else "claude",
            source="structured" if index < 5 else "legacy_text",
            parse_error="missing JSON" if index == 4 else None,
        )
    path = tmp_path / "review_protocol_observations.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write("not json\n[]\n")
    summary = summarize_review_protocol_observations(cfg, window=50)
    assert summary["status"] == "ok"
    assert summary["total"] == 6
    assert summary["structured"] == 5
    assert summary["legacy_text"] == 1
    assert summary["parse_errors"] == {"missing JSON": 1}
    assert summary["reviewers"]["codex"]["total"] == 4
    assert summary["reviewers"]["claude"]["legacy_text"] == 1
    assert summarize_review_protocol_observations(cfg, window=3)["total"] == 3
