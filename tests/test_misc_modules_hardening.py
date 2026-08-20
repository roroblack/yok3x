"""BUG-50 회귀 테스트 — 2026-08-19/20 misc-modules 감사에서 발견한 엣지 입력 방어.

codex의 감사 배치가 코드 수정은 했지만 회귀 테스트를 실제로 남기지 못해(동시 실행 중이던
다른 배치와 같은 신규 테스트 파일을 공유하다 유실된 것으로 추정) 직접 작성했다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yok3x import artifacts, calibration, knot, mcp_policy, triage, worktree


def test_bad_path_rejects_non_string_input():
    assert artifacts._bad_path(None) != ""
    assert artifacts._bad_path(123) != ""


def test_bad_path_still_accepts_normal_relative_path():
    assert artifacts._bad_path("sub/dir/file.txt") == ""


def test_calibration_read_keeps_only_last_window_with_large_input(tmp_path):
    path = tmp_path / "calibration.jsonl"
    lines = [
        '{"pattern": "p", "backend": "codex", "effort": "low", "reviewer": "r", '
        f'"bucket": "b", "score": {i % 10}, "verify_ok": true, "rounds": 1}}'
        for i in range(500)
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    result = calibration.read_calibration_jsonl(path, window=20)
    assert len(result["records"]) == 20
    assert result["reasons"]["outside_window"] == 480


def test_calibration_read_negative_window_returns_no_records(tmp_path):
    path = tmp_path / "calibration.jsonl"
    path.write_text(
        '{"pattern": "p", "backend": "codex", "effort": "low", "reviewer": "r", '
        '"bucket": "b", "score": 5, "verify_ok": true, "rounds": 1}\n',
        encoding="utf-8",
    )
    result = calibration.read_calibration_jsonl(path, window=-5)
    assert result["records"] == []


def test_aggregate_calibration_statistics_skips_malformed_records():
    records = [
        "not a dict",
        {"pattern": "p", "backend": "codex", "effort": "low", "reviewer": "r", "score": "not-a-number"},
        {"pattern": "p", "backend": "codex", "effort": "low", "reviewer": "r", "score": 7, "verify_ok": "yes"},
        {"pattern": "p", "backend": "codex", "effort": "low", "reviewer": "r", "score": 7, "verify_ok": True},
    ]
    result = calibration.aggregate_calibration_statistics(records)
    assert result["reasons"]["schema_mismatch"] == 3
    assert len(result["groups"]) == 1
    assert result["groups"][0]["sample_count"] == 1


def test_knot_recency_weight_handles_mixed_tz_awareness():
    aware_created = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    naive_now = datetime(2026, 6, 1)
    assert 0.0 <= knot._recency_weight(aware_created, naive_now, halflife_days=30.0) <= 1.0

    naive_created = datetime(2026, 1, 1).isoformat()
    aware_now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert 0.0 <= knot._recency_weight(naive_created, aware_now, halflife_days=30.0) <= 1.0


def test_resolve_mcp_grant_rejects_non_dict_inputs():
    grant = mcp_policy.resolve_mcp_grant(None, {"mcp_tools": {"servers": ["fs"]}})
    assert grant.active is False
    assert grant.denied_reason

    grant2 = mcp_policy.resolve_mcp_grant({"fs": {}}, None)
    assert grant2.active is False
    assert grant2.denied_reason


def test_resolve_mcp_grant_ignores_non_string_server_names():
    grant = mcp_policy.resolve_mcp_grant(
        {"fs": {"allow_tools": ["mcp__fs__read"]}},
        {"mcp_tools": {"servers": ["fs", 123, None], "allow_tools": ["mcp__fs__read"]}},
    )
    assert grant.active is True
    assert "fs" in grant.servers


def test_axes_falls_back_on_invalid_max_rounds():
    spec = {"pattern": "producer-reviewer", "max_rounds": "not-a-number"}
    result = triage._axes(spec)   # 예외 없이 rounds=기본값(2)으로 폴백
    assert result["cost"] in ("low", "mid", "high")


def test_worktree_remove_does_not_rmtree_unregistered_path(tmp_path, monkeypatch):
    dest = tmp_path / "not-a-real-worktree"
    dest.mkdir()

    def fake_git(args, cwd=None):
        if args[:2] == ["worktree", "remove"]:
            return False, "fatal: not a working tree"
        if args[:2] == ["worktree", "list"]:
            return True, ""   # dest는 등록된 worktree가 아님
        if args[:2] == ["worktree", "prune"]:
            return True, ""
        return False, "unexpected"

    monkeypatch.setattr(worktree, "_git", fake_git)
    calls = []
    monkeypatch.setattr(worktree.shutil, "rmtree", lambda *a, **k: calls.append(a))

    ok, _ = worktree.remove(tmp_path, dest)
    assert ok is False
    assert calls == []          # 등록 안 된 경로는 절대 rmtree 하지 않는다
    assert dest.exists()


def test_worktree_remove_rmtrees_registered_orphan_path(tmp_path, monkeypatch):
    dest = tmp_path / "orphan-worktree"
    dest.mkdir()

    def fake_git(args, cwd=None):
        if args[:2] == ["worktree", "remove"]:
            return False, "fatal: worktree registered but path missing metadata"
        if args[:2] == ["worktree", "list"]:
            return True, f"worktree {dest}\nHEAD 0000000000000000000000000000000000000000\n"
        if args[:2] == ["worktree", "prune"]:
            return True, ""
        return False, "unexpected"

    monkeypatch.setattr(worktree, "_git", fake_git)
    calls = []
    monkeypatch.setattr(worktree.shutil, "rmtree", lambda *a, **k: calls.append(a))

    worktree.remove(tmp_path, dest)
    assert calls and str(calls[0][0]) == str(dest)
