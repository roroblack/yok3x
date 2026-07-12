"""yok3x CLI.

  yok3x init [--mock]                프로젝트 초기화(설정·디렉터리 생성)
  yok3x setup                         "멀티에이전트 시스템 구성해줘" 자동 셋팅
  yok3x run <task.json> [--auto]      태스크 1회 실행(승인 게이트 포함)
  yok3x loop <task.json> -n N         에이전트 루프(가드가 스스로 정지)
  yok3x mat [--watch]                 사용량·코칭·진행 상태 한 화면
  yok3x coach                         사용량 코칭 메시지 출력
  yok3x coach guard on|off            요금 가드 on/off
  yok3x knot save|ingest|query|lint   지식그물
  yok3x flavor [이름]                 flavor 확인/변경
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, knot, matview, usage
from .config import Config, scaffold
from .orchestrator import run_loop, run_task_file

SAMPLE_TASKS = {
    # 구현 → 코드 리뷰 → 재작업 루프 (한 모델이 만들고 다른 모델이 리뷰)
    "task-producer-reviewer.json": {
        "pattern": "producer-reviewer",
        "task": "요청: 문자열을 받아 유효한 슬러그(소문자·하이픈)로 변환하는 함수를 작성하라. "
                "유니코드·연속 공백·앞뒤 구분자·빈 입력 엣지케이스를 처리하고 간단한 단위 테스트도 포함하라.",
        "producer": "claude-main",
        "reviewer": "codex-critic",
        "max_rounds": 3,
        "pass_score": 8.0
    },
    # 파이프라인: 설계 → 구현 → 코드 리뷰 (이전 단계 출력이 다음 입력)
    "task-pipeline.json": {
        "pattern": "pipeline",
        "task": "인메모리 LRU 캐시 구현",
        "stages": [
            {"worker": "claude-main", "kind": "build", "task": "get/put O(1), 용량 초과 시 LRU 제거를 만족하는 인터페이스와 자료구조를 설계·명세하라."},
            {"worker": "codex-main", "kind": "build", "task": "위 설계대로 실제 코드와 단위 테스트를 구현하라."},
            {"worker": "codex-critic", "kind": "review", "task": "구현을 리뷰하라. 버그·엣지케이스·복잡도를 점검하고 SCORE와 수정 지시를 제시하라."}
        ]
    },
    # Fan-out/Fan-in: 여러 모델이 각자 접근법을 제안 → 하나로 통합
    "task-fanout.json": {
        "pattern": "fanout-fanin",
        "task": "대용량 CSV를 스트리밍으로 파싱해 집계하는 방법을 각자 관점(메모리/속도/단순성)에서 제안하라.",
        "workers": ["claude-main", "codex-main", "gemini"],
        "join_worker": "claude-main"
    }
}


def main(argv: list[str] | None = None) -> int:
    # Windows 레거시 콘솔(cp949)에서 mat의 유니코드 게이지 출력이 깨지지 않도록
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="yok3x",
                                description=f"yok3x 멀티 에이전트 v{__version__} — Claude Code · Codex · Gemini CLI 오케스트레이터")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="초기화: yok3x.json/backends.json/디렉터리 생성")
    sp.add_argument("--mock", action="store_true", help="모든 워커를 mock 백엔드로(드라이런)")

    sub.add_parser("setup", help="샘플 태스크 3종 생성 + 초기화(자동 셋팅)")

    sp = sub.add_parser("run", help="태스크 파일 1회 실행")
    sp.add_argument("task_file")
    sp.add_argument("--auto", action="store_true", help="승인 게이트 자동 통과")

    sp = sub.add_parser("loop", help="에이전트 루프 실행(요금 가드가 스스로 정지)")
    sp.add_argument("task_file")
    sp.add_argument("-n", "--iterations", type=int, default=3)
    sp.add_argument("--gated", action="store_true", help="루프에서도 승인 게이트 사용")

    sp = sub.add_parser("mat", help="사용량·코칭·진행 상태 모니터링")
    sp.add_argument("--watch", action="store_true")
    sp.add_argument("--interval", type=float, default=2.0)

    sp = sub.add_parser("coach", help="사용량 코칭 / guard on|off")
    sp.add_argument("args", nargs="*", help="(비움)=코칭 출력 | guard on | guard off")

    sp = sub.add_parser("knot", help="지식그물: save/ingest/query/lint")
    ksub = sp.add_subparsers(dest="kcmd", required=True)
    k = ksub.add_parser("save"); k.add_argument("title"); k.add_argument("body", nargs="?")
    k.add_argument("--tags", default="")
    k = ksub.add_parser("ingest"); k.add_argument("path")
    k = ksub.add_parser("query"); k.add_argument("q", nargs="+"); k.add_argument("--limit", type=int, default=5)
    ksub.add_parser("lint")

    sp = sub.add_parser("flavor", help="flavor 확인/변경")
    sp.add_argument("name", nargs="?")

    sub.add_parser("limits", help="실제 구독 한도 probe 원본 확인(진단)")

    sp = sub.add_parser("gui", help="브라우저 GUI 프로토타입(실데이터) 실행")
    sp.add_argument("--port", type=int, default=8760)
    sp.add_argument("--no-open", action="store_true", help="브라우저 자동 실행 안 함")

    sp = sub.add_parser("plan", help="요금제 프리셋 확인/설정(claude/gemini). codex는 자동감지")
    sp.add_argument("tool", nargs="?")
    sp.add_argument("name", nargs="?")

    sp = sub.add_parser("profile", help="상황별 모델 프로파일 확인/설정(best|balanced|cost|speed|off)")
    sp.add_argument("mode", nargs="?")

    sp = sub.add_parser("calibrate", help="실제 사용률로 claude 한도 역산 보정(정확)")
    sp.add_argument("tool")
    sp.add_argument("window", choices=["5h", "7d"])
    sp.add_argument("percent", type=float)

    a = p.parse_args(argv)
    cfg = Config.load(".")

    if a.cmd == "init":
        cfg = scaffold(".", use_mock=a.mock)
        print(f"초기화 완료: {cfg.paths.yok3x_json}, {cfg.paths.backends_json}")
        print(f"flavor={cfg.yok3x['flavor']}, guard={'ON' if cfg.yok3x['guard']['enabled'] else 'OFF'}"
              + (", 백엔드=mock(드라이런)" if a.mock else ""))
        return 0

    if a.cmd == "setup":
        cfg = scaffold(".")
        for name, spec in SAMPLE_TASKS.items():
            f = Path(name)
            if not f.exists():
                f.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("멀티에이전트 시스템 구성 완료. 샘플: " + ", ".join(SAMPLE_TASKS))
        print("실행 예: yok3x run task-producer-reviewer.json --auto")
        return 0

    if a.cmd == "run":
        state = run_task_file(cfg, a.task_file, auto=a.auto or None)
        print(f"\n종료 상태: {state}")
        return 0 if state == "done" else 1

    if a.cmd == "loop":
        run_loop(cfg, a.task_file, iterations=a.iterations, auto=not a.gated)
        return 0

    if a.cmd == "mat":
        matview.show(cfg, watch=a.watch, interval=a.interval)
        return 0

    if a.cmd == "coach":
        if a.args[:1] == ["guard"]:
            if a.args[1:2] == ["on"]:
                usage.guard_toggle(cfg, True); print("guard: ON")
            elif a.args[1:2] == ["off"]:
                usage.guard_toggle(cfg, False); print("guard: OFF")
            else:
                print("사용법: yok3x coach guard on|off"); return 2
            return 0
        for m in usage.coach_messages(cfg):
            print("· " + m)
        return 0

    if a.cmd == "limits":
        from . import limits
        for b in usage.BACKEND_KEYS:
            r = limits.probe(cfg, b, use_cache=False)
            src = "실측" if r.real else ("추정" if r.ok else "-")
            print(f"[{b}] type={r.source}  ok={r.ok}  {src}")
            for w in r.windows:
                print(f"    {w.name:>3}  {w.used_percent:5.1f}%  리셋 {w.reset_in()}")
            if r.detail:
                print(f"    detail: {r.detail}")
            if r.error:
                print(f"    ! {r.error}")
        return 0

    if a.cmd == "gui":
        from . import guiserver
        guiserver.serve(cfg, port=a.port, open_browser=not a.no_open)
        return 0

    if a.cmd == "plan":
        from .config import PLAN_PRESETS
        if a.tool and a.name:
            presets = PLAN_PRESETS.get(a.tool, {})
            if a.name not in presets:
                print(f"없는 plan: {a.name} (가능: {', '.join(presets) or '없음'})")
                return 2
            cfg.yok3x.setdefault("limits", {}).setdefault(a.tool, {})["plan"] = a.name
            cfg.save_yok3x()
            print(f"{a.tool} plan = {a.name}  {presets[a.name]}")
            print("정확도는 `yok3x calibrate claude 7d <실제%>` 로 보정 권장")
        else:
            for tool, presets in PLAN_PRESETS.items():
                cur = (cfg.yok3x.get("limits", {}).get(tool, {}) or {}).get("plan") or "(미설정)"
                print(f"{tool}: {cur}   가능: {', '.join(presets)}")
            print("codex: app-server가 plan 자동 감지(설정 불필요)")
        return 0

    if a.cmd == "profile":
        from .orchestrator import resolve_model
        profiles = cfg.yok3x.get("profiles", {})
        if a.mode is not None:
            if a.mode not in profiles and a.mode not in ("off", ""):
                print(f"없는 프로파일: {a.mode} (가능: {', '.join(profiles)}, off)")
                return 2
            cfg.yok3x["active_profile"] = "" if a.mode in ("off", "") else a.mode
            cfg.save_yok3x()
        cur = cfg.yok3x.get("active_profile") or ""
        print(f"현재 프로파일: {cur or '(off — 워커 기본 backend·CLI 기본 모델)'}")
        print(f"가능: {', '.join(profiles)}  (off로 끄기)")
        if cur:
            print("상황별 라우팅 미리보기:")
            for tk in ("build", "review", "design_review"):
                b, m, why = resolve_model(cfg, tk)
                print(f"  {tk:14s} → {(b or '(기본)')}{'/' + m if m else ''}   {('[' + why + ']') if why else ''}")
        return 0

    if a.cmd == "calibrate":
        from . import limits
        if a.tool != "claude":
            print("현재 calibrate는 claude만 지원(codex는 라이브 실측이라 불필요)")
            return 2
        if a.percent <= 0:
            print("percent는 0보다 커야 함")
            return 2
        conf = cfg.yok3x.get("limits", {}).get("claude", {})
        toks = limits.claude_rolling_tokens(conf, a.window)
        cap = int(toks / (a.percent / 100.0))
        key = "limit_5h_tokens" if a.window == "5h" else "limit_7d_tokens"
        cfg.yok3x.setdefault("limits", {}).setdefault("claude", {})[key] = cap
        cfg.save_yok3x()
        print(f"claude {a.window}: 현재 {toks:,}tok = {a.percent:.0f}% → {key} = {cap:,} 설정")
        print("이후 이 창 사용률은 이 상한 기준으로 정확히 계산된다.")
        return 0

    if a.cmd == "knot":
        if a.kcmd == "save":
            body = a.body if a.body else sys.stdin.read()
            tags = [t.strip() for t in a.tags.split(",") if t.strip()]
            path = knot.save(cfg, a.title, body, tags=tags)
            print(f"저장: {path}")
        elif a.kcmd == "ingest":
            out = knot.ingest(cfg, a.path)
            print(f"가져옴: {len(out)}개 → {cfg.paths.knowledge}")
        elif a.kcmd == "query":
            hits = knot.query(cfg, " ".join(a.q), limit=a.limit)
            if not hits:
                print("(결과 없음)")
            for score, n in hits:
                print(f"[{score:.0f}] {n.get('title')}  ({n['path'].name})")
                print("     " + n.get("body", "").strip().replace("\n", " ")[:100])
        elif a.kcmd == "lint":
            issues = knot.lint(cfg)
            if not issues:
                print("lint: 문제 없음")
            for i in issues:
                print("· " + i)
            return 1 if issues else 0
        return 0

    if a.cmd == "flavor":
        if a.name:
            if a.name not in cfg.yok3x["flavors"]:
                print(f"없는 flavor: {a.name} (가능: {list(cfg.yok3x['flavors'])})")
                return 2
            cfg.yok3x["flavor"] = a.name
            cfg.save_yok3x()
        f = cfg.flavor()
        print(f"flavor={cfg.yok3x['flavor']}  orchestrator={f['orchestrator']}")
        print(f"workers: {', '.join(f['workers'])}")
        return 0

    return 0


def entry() -> int:
    try:
        return main()
    except BrokenPipeError:      # `yok3x mat | head` 등 파이프 조기 종료
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(entry())
