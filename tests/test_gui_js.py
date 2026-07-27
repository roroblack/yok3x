"""F2-8: GUI 순수 함수 JS 테스트를 pytest 한 진입점에서 함께 돌린다.

러너 결정(2026-07-27): **Node 내장 `node:test`** — 의존성 0 원칙에 유일하게 맞는 선택이다
(jest/vitest/mocha는 전부 npm 의존성). Node가 없는 환경에서는 skip 한다 — Python 쪽 테스트는
Node 없이도 전부 돌아야 하므로 하드 요구사항으로 만들지 않는다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "gui_pure.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음 — JS 테스트 skip")
def test_gui_pure_js_suite_passes():
    """gui/index.html의 순수 함수(esc·임계분류·포매터)를 GUI 수정 없이 검증한다.
    esc()는 BUG-19(산출물 HTML이 DOM을 파괴)의 회귀 방지선이라 특히 중요하다."""
    proc = subprocess.run(
        [shutil.which("node"), "--test", str(JS_TEST)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(JS_TEST.parents[2]), timeout=120)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    assert "fail 0" in (proc.stdout or "")
