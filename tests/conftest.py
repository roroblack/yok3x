"""테스트 격리 안전장치 (F2-1 · 2026-07-22).

mock이 무력화되거나(config `use_mock`/type 오설정) 새 테스트가 mock 배선을 빠뜨리면, yok3x 코드는
실제 backend(claude/codex/gemini CLI 서브프로세스, Anthropic/OpenAI/Google 네트워크 호출)를 그대로
때린다. 관측된 실패모드: 최대 수백 초 행 + **실쿼터 소비**(N0'에서 실제로 겪음).

이중 안전장치:
  1) `@pytest.mark.live` 없는 모든 테스트는 backend 실행파일(claude/codex/gemini) 호출과 urlopen을
     **차단**(RuntimeError). subprocess.run/Popen 자체는 막지 않는다 — `verify_cmd`(orchestrator가
     사용자 작업의 테스트를 실제로 실행하는 기능, 항상 real·안전·비용 없음)나 프로세스 종료 테스트처럼
     backend가 아닌 정당한 로컬 실행은 그대로 통과시키고, 실행 대상이 claude/codex/gemini 실행파일일
     때만 막는다(`usage.BACKEND_KEYS` 기준). urlopen은 테스트에서 real 호출이 정당한 경우가 없어
     예외 없이 차단한다.
  2) `live`로 마킹된 테스트도 `YOK3X_ALLOW_LIVE=1` 환경변수가 없으면 **스킵**(실행 자체를 안 함).
     마커만으로는 실행되지 않는다 — 실수로 라이브 테스트가 CI/로컬에서 도는 사고를 막는다.

기존 테스트가 `monkeypatch.setattr(backends.subprocess, "run", fake)`처럼 자체 스텁을 놓으면, 같은
모듈 객체(subprocess)를 나중에 setattr하므로 이 차단을 정상적으로 덮어쓰고 그 테스트의 스텁이 쓰인다.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import urllib.request

import pytest

from yok3x.usage import BACKEND_KEYS   # ("claude", "codex", "gemini") — 차단 대상 실행파일명


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: 실제 backend(claude/codex/gemini) 호출을 의도적으로 허용하는 테스트. "
        "YOK3X_ALLOW_LIVE=1 환경변수 없으면 스킵된다(F2-1 이중 안전장치).",
    )


def _program_name(cmd) -> str:
    """subprocess 호출 대상의 실행파일 베이스이름(경로·확장자 제거, 소문자). list/문자열(shell=True) 둘 다."""
    if isinstance(cmd, (list, tuple)):
        first = str(cmd[0]) if cmd else ""
    else:
        try:
            parts = shlex.split(str(cmd), posix=(os.name != "nt"))
        except ValueError:
            parts = str(cmd).split()
        first = parts[0] if parts else ""
    name = os.path.basename(first).lower()
    for ext in (".exe", ".cmd", ".bat"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return name


def _is_backend_cmd(cmd) -> bool:
    """claude/codex/gemini 실행파일 호출인가(verify_cmd·프로세스 관리용 실행은 여기 안 걸림)."""
    return _program_name(cmd) in BACKEND_KEYS


def _blocked_reason(what: str) -> str:
    return (
        f"yok3x 테스트 안전장치(F2-1): 실제 backend 호출이 차단됐다({what}). "
        "이 테스트는 mock 배선이 빠졌다 — config type을 'mock'으로 하거나 monkeypatch로 "
        "스텁하라. 정말 라이브 호출이 필요하면 @pytest.mark.live + 환경변수 YOK3X_ALLOW_LIVE=1 을 쓰라."
    )


@pytest.fixture(autouse=True)
def _block_real_backend_calls(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """모든 테스트에 자동 적용(autouse). live 마커+env가 둘 다 있을 때만 실제 호출을 허용한다."""
    if request.node.get_closest_marker("live") is not None:
        if os.environ.get("YOK3X_ALLOW_LIVE") != "1":
            pytest.skip("live 테스트는 YOK3X_ALLOW_LIVE=1 환경변수가 있어야 실행된다(F2-1 안전장치).")
        return   # 명시적 이중 opt-in 완료 — 실제 호출 허용, 패치하지 않음

    real_run, real_popen = subprocess.run, subprocess.Popen

    def guarded_run(cmd, *args, **kwargs):
        if _is_backend_cmd(cmd):
            raise RuntimeError(_blocked_reason(f"subprocess.run {cmd!r}"))
        return real_run(cmd, *args, **kwargs)

    def guarded_popen(cmd, *args, **kwargs):
        if _is_backend_cmd(cmd):
            raise RuntimeError(_blocked_reason(f"subprocess.Popen {cmd!r}"))
        return real_popen(cmd, *args, **kwargs)

    def blocked_urlopen(*_args, **_kwargs):
        raise RuntimeError(_blocked_reason("urllib.request.urlopen"))

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    monkeypatch.setattr(urllib.request, "urlopen", blocked_urlopen)
