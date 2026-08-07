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
import time
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

    sp = sub.add_parser("calib", help="심판 캘리브레이션 요약(SCORE가 실제 통과를 예측하나)")
    sp.add_argument("--threshold", type=float, default=8.0, help="게이트 임계 SCORE(기본 8.0)")

    sp = sub.add_parser("knot", help="지식그물: save/ingest/query/lint")
    ksub = sp.add_subparsers(dest="kcmd", required=True)
    k = ksub.add_parser("save"); k.add_argument("title"); k.add_argument("body", nargs="?")
    k.add_argument("--tags", default="")
    k = ksub.add_parser("ingest"); k.add_argument("path")
    k = ksub.add_parser("query"); k.add_argument("q", nargs="+"); k.add_argument("--limit", type=int, default=5)
    ksub.add_parser("lint")

    sp = sub.add_parser("flavor", help="flavor 확인/변경")
    sp.add_argument("name", nargs="?")

    sp = sub.add_parser("limits", help="실제 구독 한도 probe 원본 확인(진단)")
    sp.add_argument("--json", action="store_true",
                    help="기계판독 스냅샷(JSON) 출력 — provenance enum 포함")
    sp.add_argument("--exit-code", action="store_true",
                    help="자동화용 종료코드: 0=ok · 3=warn(soft 도달) · 4=stop(hard 도달)")
    sub.add_parser("statusline", help="Claude Code statusLine 핸들러(stdin JSON의 rate_limits 캡처+상태줄 출력)")

    sp = sub.add_parser("claude-usage",
                        help="Claude Code 로컬 JSONL 세션·모델별 토큰 귀속(R-5, Tier2·사후감사용 — 페이싱 앵커 아님)")
    sp.add_argument("--days", type=float, default=7.0, help="최근 N일만 집계(기본 7, 0=전체)")
    sp.add_argument("--json", action="store_true", help="기계판독 JSON 출력")

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

    sp = sub.add_parser("pace", help="하루 페이싱(주간쿼터 하루 소비 캡) 상태/승인재개")
    sp.add_argument("action", nargs="?", choices=["status", "approve"], default="status")
    sp.add_argument("backend", nargs="?", help="approve 대상 backend(claude/codex)")
    sp.add_argument("--json", action="store_true", help="기계판독 스냅샷(JSON) 출력")
    sp.add_argument("--exit-code", action="store_true",
                    help="자동화용 종료코드: 0=ok · 3=warn · 4=stop(페이싱 정지)")

    sp = sub.add_parser("review", help="review bundle 표시·수락·거절")
    sp.add_argument("run_id")
    action = sp.add_mutually_exclusive_group()
    action.add_argument("--accept", nargs="*", metavar="PATH",
                        help="후보 적용(경로 생략 시 unchanged 외 전체)")
    action.add_argument("--reject", action="store_true", help="트리 변경 없이 거절 기록")

    a = p.parse_args(argv)

    if a.cmd == "statusline":
        # Claude Code가 매 렌더마다 임의 cwd에서 stdin JSON과 함께 호출한다. 프로젝트 config를 로드하지
        # 않고(부작용·지연 방지) 경량 처리 — rate_limits를 홈 캐시(~/.yok3x/statusline.json)에 저장하고
        # 상태줄만 출력. 실패해도 exit 0(비어 있으면 Claude Code가 빈 줄 표시)로 렌더를 막지 않는다.
        from . import limits
        try:
            print(limits.statusline_capture({}, sys.stdin.read()))
        except Exception:
            print("")
        return 0

    cfg = Config.load(".")

    if a.cmd == "review":
        from . import review as review_bundle
        try:
            root = review_bundle.find_bundle(cfg, a.run_id)
            bundle = review_bundle.load_bundle(root)
            if a.accept is not None:
                return 1 if review_bundle.accept_bundle(root, bundle, a.accept) else 0
            if a.reject:
                review_bundle.reject_bundle(root, a.run_id)
                return 0
            review_bundle.show_bundle(root, bundle)
            return 0
        except review_bundle.ReviewError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2

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
        sink: dict = {}
        state = run_task_file(cfg, a.task_file, auto=a.auto or None, sink=sink)
        print(f"\n종료 상태: {state}")
        gate = sink.get("gate")
        # 종료 상태(실행 생명주기)와 게이트(산출물 승인)를 분리한다(F2-2). done이어도 gate.passed=false
        # (strict 저점 탈락·verify 실패 등)면 자동화가 성공으로 소비하면 안 되므로 별도 종료코드(3)로 낸다.
        # 1=실행 실패/중단, 3=실행은 done이나 산출물 미승인, 0=완료+승인.
        if state != "done":
            return 1
        if isinstance(gate, dict) and not gate.get("passed", True):
            print(f"[gate] 산출물 미승인({gate.get('mode')}): {gate.get('reason')} · "
                  f"score={gate.get('score')}/{gate.get('threshold')} "
                  f"verify_ok={gate.get('verify_ok')}", file=sys.stderr)
            return 3
        return 0

    if a.cmd == "loop":
        run_loop(cfg, a.task_file, iterations=a.iterations, auto=not a.gated)
        return 0

    if a.cmd == "mat":
        matview.show(cfg, watch=a.watch, interval=a.interval)
        return 0

    if a.cmd == "calib":
        import json as _json
        from . import calibration
        p = cfg.paths.runs.parent / "calibration.jsonl"
        recs = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        recs.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        pass
        s = calibration.summarize(recs, threshold=a.threshold)
        corr = s["score_verify_corr"]
        c = s["confusion"]
        print(f"심판 캘리브레이션 — 레코드 {s['n_total']}개(라벨 {s['n_labeled']}개, verify_cmd 있는 런만)")
        print(f"  판정: {s['verdict']}")
        if s["pass_rate"] is not None:
            print(f"  실제 통과율: {s['pass_rate']*100:.0f}%")
        print(f"  SCORE↔통과 상관: {'%.3f' % corr if corr is not None else '—(표본/분산 부족)'}"
              f"   (~0이면 게이트가 신호를 못 줌)")
        print(f"  게이트@{c['threshold']:.1f}: 통과시킴 중 실제통과 "
              f"{'%.0f%%' % (c['precision']*100) if c['precision'] is not None else '—'}"
              f"  (tp={c['tp']} fp={c['fp']} tn={c['tn']} fn={c['fn']})")
        if s["n_labeled"] < 10:
            print("  ※ 표본이 적어(10 미만) 아직 신뢰 불가 — 런이 쌓이면 다시 보라.")
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
        readings = {b: limits.probe(cfg, b, use_cache=False) for b in usage.BACKEND_KEYS}
        levels = []
        if a.json:
            # R-4 기계판독 스냅샷: 사람용 문구(detail) 파싱 없이 소비할 수 있게 provenance enum과
            # 수치 필드를 그대로 노출한다. 훅/CI가 이 계약에 붙는다.
            snap = {"schema": "yok3x.limits/1", "at": time.time(), "backends": {}}
            for b, r in readings.items():
                lv = usage.check_backend(cfg, b).level if r.ok else "unknown"
                levels.append(lv)
                snap["backends"][b] = {
                    "source": r.source, "provenance": r.provenance(),
                    "ok": r.ok, "real": r.real, "level": lv,
                    "ratio": round(r.ratio(), 4),
                    "windows": [{"name": w.name, "used_percent": w.used_percent,
                                 "resets_at": w.resets_at, "window_minutes": w.window_minutes,
                                 "used_tokens": w.used_tokens, "limit_tokens": w.limit_tokens}
                                for w in r.windows],
                    "detail": r.detail, "error": r.error,
                }
            print(json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            for b, r in readings.items():
                src = "실측" if r.real else ("추정" if r.ok else "-")
                print(f"[{b}] type={r.source}  ok={r.ok}  {src}  provenance={r.provenance()}")
                for w in r.windows:
                    print(f"    {w.name:>3}  {w.used_percent:5.1f}%  리셋 {w.reset_in()}")
                if r.detail:
                    print(f"    detail: {r.detail}")
                if r.error:
                    print(f"    ! {r.error}")
                if r.ok:
                    levels.append(usage.check_backend(cfg, b).level)
        if a.exit_code:      # 자동화: `yok3x limits --exit-code && 큰작업`
            if "stop" in levels:
                return 4
            if "warn" in levels:
                return 3
        return 0

    if a.cmd == "claude-usage":
        from . import limits
        conf = (cfg.yok3x.get("limits") or {}).get("claude") or {}
        now = time.time()
        since = (now - a.days * 86400.0) if a.days > 0 else None
        rows = limits.claude_usage_breakdown(conf, since=since, until=now)
        if a.json:
            print(json.dumps({"schema": "yok3x.claude_usage/1", "since": since, "until": now,
                              "rows": rows}, ensure_ascii=False, indent=2))
            return 0
        span = f"최근 {a.days:g}일" if a.days > 0 else "전체 기간"
        print(f"Claude Code 세션·모델별 토큰 귀속({span}, Tier2·사후감사용 — 페이싱 앵커 아님)")
        if not rows:
            print("  (데이터 없음 — JSONL 미발견/파싱 실패. 페이싱엔 영향 없음, 공식 reading이 앵커)")
            return 0
        for r in rows[:30]:
            sid = (r["session_id"] or "")[:8] or "?"
            last = time.strftime("%m-%d %H:%M", time.localtime(r["last_ts"]))
            print(f"  {sid}  {r['model'] or '?':<24s}  {r['tokens']:>10,}tok  {r['calls']:>4}회  ~{last}")
        if len(rows) > 30:
            print(f"  ...외 {len(rows) - 30}건(세션·모델 조합)")
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
            print("상황별 라우팅 미리보기(가용성·한도 반영):")
            avail = lambda b: usage.backend_available(cfg, b)
            for tk in ("build", "review", "design_review"):
                b, m, why = resolve_model(cfg, tk, available=avail)
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

    if a.cmd == "pace":
        from . import limits
        if a.action == "approve":
            if not a.backend:
                print("사용법: yok3x pace approve <backend>"); return 2
            usage.pace_approve(cfg, a.backend)
            cfg.save_yok3x()
            print(f"{a.backend}: 하루 페이싱 정지를 오늘 하루 해제(승인). 자정에 자동 리셋.")
            return 0
        dp = cfg.yok3x.get("guard", {}).get("daily_pace", {})
        states: dict[str, dict | None] = {}
        provenances: dict[str, str] = {}
        for b in usage.BACKEND_KEYS:
            r = limits.probe(cfg, b)
            _ra = usage.effective_reset_at(cfg, b, r)
            wk, _known, _tu = usage._pace_inputs(cfg, b, r, _ra)
            states[b] = usage.daily_pace_status(
                cfg, b, wk, today=usage._pacing_day_key(_ra),
                reset_at=_ra, today_used=_tu, since_reset_known=_known)
            provenances[b] = r.provenance()
        if a.json:
            snap = {"schema": "yok3x.pace/1", "at": time.time(),
                    "enabled": bool(dp.get("enabled")),
                    "pct_of_weekly": float(dp.get("pct_of_weekly", 0.14)),
                    "mode": dp.get("mode", "warn"), "backends": {}}
            for b, st in states.items():
                snap["backends"][b] = None if not st else {
                    "provenance": provenances[b], "used": round(st["used"], 2),
                    "cap": round(st["cap"], 2),
                    "soft": round(st["soft"], 2), "base_cap": round(st["base_cap"], 2),
                    "even_cap": st.get("even_cap"), "forward_daily": st.get("forward_daily"),
                    "strategy": st["strategy"], "level": st["level"],
                    "blocked": st["blocked"], "approved": st["approved"],
                }
            print(json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            print(f"하루 페이싱: {'ON' if dp.get('enabled') else 'OFF'}  "
                  f"캡={float(dp.get('pct_of_weekly', 0.2)) * 100:.0f}%  mode={dp.get('mode', 'warn')}")
            for b, st in states.items():
                if st:
                    print(f"  {b:7s} 오늘소비 {st['used']:.0f}/{st['cap']:.0f}%  "
                          f"level={st['level']}{' (승인됨)' if st['approved'] else ''}")
                else:
                    print(f"  {b:7s} (페이싱 off 또는 7d 실측 없음)")
        if a.exit_code:
            levels = [st["level"] for st in states.values() if st]
            if "stop" in levels:
                return 4
            if "warn" in levels:
                return 3
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
