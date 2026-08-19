import json

from yok3x.review_protocol import (
    PROTOCOL_VERSION,
    SEVERITIES,
    canonical_defect_signature,
    extract_json_candidate,
    parse_review_response,
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
