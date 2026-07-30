"""F2-7 수용 기준 테스트 — **구현 전에 먼저 쓴다**(계약서 §3).

계약: docs/reports/v4.5.0-f2-7-parallel-resume-contract-2026-07-27-1830.md
- C-1 재생 단위는 prefix가 아니라 **집합**
- C-2 손상 파일은 **그 항목만** 제외(전체 중단 아님)
- C-4 materialize/changes는 run_id 격리라 재개 허용
- C-6 실행 순서는 재현하지 않는다(호출 결과만 재현)
"""
from __future__ import annotations

import json

from yok3x import orchestrator
from yok3x.config import Config, scaffold


def _step(run_dir, index, worker, call_key, ok=True, text="out"):
    """정상 스키마의 step 파일 하나를 쓴다(재생 캐시가 읽는 최소 필드)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"step_{index:02d}_{worker}.json").write_text(json.dumps({
        "index": index, "worker": worker, "task_kind": "build", "task": "t",
        "backend": "mock", "model": None, "read_only": False,
        "call_key": call_key, "ok": ok, "error": "", "text": text,
        "score": None, "checklist": [],
        "usage": {"tokens": 1, "cost_usd": 0.0, "duration_ms": 1,
                  "input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
    }, ensure_ascii=False), encoding="utf-8")


def test_c1_replay_is_a_set_not_a_prefix(tmp_path):
    """C-1: 1번이 실패해도 **2·3번의 성공 결과는 재생 대상으로 살아야** 한다.
    병렬은 완료가 집합이라 '연속 prefix' 요구는 무관한 워커의 결과까지 버린다."""
    run = tmp_path / "run_x"
    _step(run, 1, "claude-main", "KEY-A", ok=False)      # 실패
    _step(run, 2, "codex-main", "KEY-B")                 # 성공
    _step(run, 3, "gemini", "KEY-C")                     # 성공

    cache, _reason = orchestrator._load_replay_steps(run)

    assert set(cache) == {"KEY-B", "KEY-C"}              # 실패한 1번만 제외
    assert cache["KEY-B"]["text"] == "out"


def test_c2_corruption_is_isolated_to_that_item(tmp_path):
    """C-2: 손상 파일은 그 항목만 버리고 나머지는 살린다. 단 **읽을 수 없는 걸 성공으로
    간주하지 않는다**(fail-closed 유지)."""
    run = tmp_path / "run_y"
    _step(run, 1, "claude-main", "KEY-A")
    (run / "step_02_codex-main.json").write_text("{깨진 JSON", encoding="utf-8")
    _step(run, 3, "gemini", "KEY-C")
    # 필수 필드 누락(usage 없음)도 그 항목만 제외돼야 한다
    (run / "step_04_codex-critic.json").write_text(
        json.dumps({"index": 4, "call_key": "KEY-D", "ok": True}), encoding="utf-8")

    cache, reason = orchestrator._load_replay_steps(run)

    assert set(cache) == {"KEY-A", "KEY-C"}
    assert "KEY-D" not in cache                          # 스키마 미달은 재생 금지
    assert "손상" in reason or "제외" in reason           # 왜 빠졌는지 알려준다


def test_c1_duplicate_index_is_rejected_for_that_index_only(tmp_path):
    """같은 번호가 둘이면 어느 쪽이 진실인지 알 수 없다 → 그 번호만 제외(나머지는 유지)."""
    run = tmp_path / "run_z"
    _step(run, 1, "claude-main", "KEY-A")
    _step(run, 2, "codex-main", "KEY-B")
    _step(run, 2, "codex-critic", "KEY-B2")               # 번호 중복
    _step(run, 3, "gemini", "KEY-C")

    cache, _ = orchestrator._load_replay_steps(run)

    assert "KEY-A" in cache and "KEY-C" in cache
    assert "KEY-B" not in cache and "KEY-B2" not in cache


def test_c4_parallel_and_changes_no_longer_block_resume(tmp_path):
    """C-4/C-1: parallel 켜짐·materialize/changes 있음도 재개를 막지 않는다.
    acquire만 계속 제외한다(C-5 — 쿼터 재소모)."""
    scaffold(tmp_path, use_mock=True)
    cfg = Config.load(tmp_path)
    cfg.yok3x["guard"]["parallel"]["enabled"] = True

    base = {"pattern": "fanout", "task": "t", "workers": ["claude-main", "codex-main"]}
    ok, reason = orchestrator._resume_supported(base, cfg)
    assert ok is True, reason                             # 병렬 fanout 허용

    ok2, _ = orchestrator._resume_supported({**base, "changes": {"mode": "review"}}, cfg)
    assert ok2 is True                                    # changes 허용(run_id 격리)
    ok3, _ = orchestrator._resume_supported(
        {**base, "materialize": {"enabled": True}}, cfg)
    assert ok3 is True                                    # materialize 허용

    ok4, why = orchestrator._resume_supported({**base, "acquire": {"qa_count": 1}}, cfg)
    assert ok4 is False and "acquire" in why              # C-5: acquire는 제외 유지
