"""artifacts 순수 모듈(산출물 게시) 테스트. materialize 커밋(bf6915b) 검증 갭 닫기."""
from yok3x import artifacts as A


# ---------------------------------------------------------------- parse_file_blocks
def test_parse_extracts_only_file_fences_not_language_fences():
    txt = ("설명\n```file:index.html\n<!doctype html>\n<h1>hi</h1>\n```\n"
           "다른 설명\n```html\n무명(저장 안 됨)\n```\n")
    blocks = A.parse_file_blocks(txt)
    assert len(blocks) == 1                                   # 언어펜스(html)는 대상 아님
    assert blocks[0].path == "index.html"
    assert blocks[0].content == "<!doctype html>\n<h1>hi</h1>"  # 닫는 펜스 앞 개행 제거


def test_parse_multiple_files():
    txt = "```file:a.py\nprint(1)\n```\n설명\n```file:src/b.js\nlet x=1\n```\n"
    blocks = A.parse_file_blocks(txt)
    assert [b.path for b in blocks] == ["a.py", "src/b.js"]


def test_parse_none_and_empty():
    assert A.parse_file_blocks("") == []
    assert A.parse_file_blocks("파일 블록 없는 그냥 텍스트") == []


def test_fileblock_sha_deterministic():
    a = A.FileBlock("x", "내용")
    b = A.FileBlock("y", "내용")
    assert a.sha256() == b.sha256()                          # 경로 무관, 내용만
    assert a.sha256() != A.FileBlock("x", "다른내용").sha256()


# ---------------------------------------------------------------- plan_files 가드
def _plan(path, **kw):
    return A.plan_files([A.FileBlock(path, "x")], **kw)


def test_plan_accepts_normal_relative_path():
    plan = _plan("src/app.js")
    assert [b.path for b in plan.accepted] == ["src/app.js"]
    assert plan.rejected == []


def test_plan_rejects_path_traversal():
    assert _plan("../etc/passwd").rejected[0]["reason"].startswith("..")
    assert _plan("a/../../b").rejected                       # 중간 .. 도


def test_plan_rejects_absolute_and_drive_and_unc():
    assert _plan("/etc/x").rejected
    assert _plan("C:/Windows/x").rejected
    assert _plan("//server/share/x").rejected


def test_plan_rejects_windows_reserved_names():
    for name in ("con", "CON.txt", "nul", "com1", "LPT9.log"):
        assert _plan(name).rejected, name


def test_plan_normalizes_whole_path_trailing_space():
    # 전체 경로 끝 공백은 strip으로 정규화돼 안전한 이름이 된다(위험 아님) — 정상 게시.
    assert [b.path for b in _plan("bad ").accepted] == ["bad"]


def test_plan_rejects_dangerous_segment_names_and_illegal_chars():
    assert _plan("bad.").rejected                            # 끝 점(strip은 점 안 지움 → 세그먼트 위험)
    assert _plan("sub /file.txt").rejected                   # 세그먼트 내부 끝 공백(Windows 위험)
    assert _plan("dir./x.txt").rejected                      # 세그먼트 끝 점
    assert _plan('a<b>.txt').rejected                        # 금지문자


def test_plan_rejects_duplicate_and_case_collision():
    plan = A.plan_files([A.FileBlock("App.js", "1"), A.FileBlock("app.js", "2")])
    assert len(plan.accepted) == 1                           # 대소문자 충돌 → 하나만
    assert any("충돌" in r["reason"] for r in plan.rejected)


def test_plan_overwrite_policy():
    existing = {"index.html"}
    assert _plan("index.html", existing=existing).rejected               # 기본: 덮어쓰기 금지
    assert _plan("index.html", existing=existing, overwrite=True).accepted  # 허용 시 통과


def test_plan_size_and_count_limits():
    big = A.FileBlock("big.txt", "x" * 1000)
    assert A.plan_files([big], max_bytes_per_file=100).rejected          # 개별 크기
    many = [A.FileBlock(f"f{i}.txt", "x") for i in range(5)]
    assert len(A.plan_files(many, max_files=3).accepted) == 3            # 개수 상한
    two = [A.FileBlock("a.txt", "x" * 60), A.FileBlock("b.txt", "x" * 60)]
    assert len(A.plan_files(two, max_total_bytes=100).accepted) == 1     # 전체 크기


# ---------------------------------------------------------------- 오케스트레이터 E2E
def _orch(tmp_path, monkeypatch, materialize):
    from yok3x.config import Config
    from yok3x import orchestrator as O, backends as B, usage as U
    monkeypatch.setattr(O.usage, "check_backend",
                        lambda c, b: U.GuardVerdict(b, 0.0, "-", "ok", "stub"))
    monkeypatch.setattr(O.usage, "record", lambda *a, **k: None)
    cfg = Config.load(tmp_path)
    cfg.yok3x["auto_approve"] = True
    cfg.yok3x["guard"]["enabled"] = False
    o = O.Orchestrator(cfg, auto=True)
    o.materialize = materialize
    return o


def test_materialize_writes_files_and_records_status(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    wd = tmp_path / "work"
    wd.mkdir()
    o = _orch(tmp_path, monkeypatch, {"enabled": True})
    o.workdir = str(wd)
    o._finish("계산기", "```file:index.html\n<h1>계산기</h1>\n```\n"
                        "```file:../escape.txt\nX\n```")            # 탈출 시도 포함
    root = wd / "yok3x-out" / o.run_id
    assert (root / "index.html").read_text(encoding="utf-8") == "<h1>계산기</h1>"
    assert not (wd.parent / "escape.txt").exists()                 # 경로탈출 차단
    assert not list(root.glob("*.tmp"))                            # 임시파일 잔여 없음
    # 텍스트 성공과 별개로 materialized가 status에 남는다(최종 저장에 덮이지 않음 — 회귀 방지)
    mat = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))["materialized"]
    assert mat["ok"] and [w["path"] for w in mat["written"]] == ["index.html"]
    assert all("sha256" in w for w in mat["written"])
    assert any(".." in r["reason"] for r in mat["rejected"])


def test_materialize_disabled_is_noop(tmp_path, monkeypatch):
    wd = tmp_path / "work2"
    wd.mkdir()
    o = _orch(tmp_path, monkeypatch, {})                          # enabled 없음
    o.workdir = str(wd)
    o._finish("x", "```file:y.txt\nz\n```")
    assert not (wd / "yok3x-out").exists()


def test_materialize_no_file_blocks_reports_not_ok(tmp_path, monkeypatch):
    import json
    o = _orch(tmp_path, monkeypatch, {"enabled": True})
    o.workdir = str(tmp_path / "w3")
    o._finish("x", "그냥 텍스트, file 블록 없음")                    # 게시할 것 없음
    mat = json.loads((o.run_dir / "status.json").read_text(encoding="utf-8"))["materialized"]
    assert mat["enabled"] and not mat["ok"] and mat["written"] == []
