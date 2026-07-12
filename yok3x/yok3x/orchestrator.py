"""오케스트레이터.

- flavor별 orchestrator/worker 구조 (yok3x.json의 flavors)
- 워크플로우 패턴: pipeline / fanout-fanin / producer-reviewer
- 승인 게이트: 각 단계 실행 전 y/n (auto_approve로 생략 가능)
- 파일 기반 로그: .yok3x/runs/<run_id>/status.json + step_NN_<worker>.json
- 검증 체크리스트: 각 단계 결과에 대해 규칙 점검 후 기록
- 요금 가드: 매 호출 전 guard_allows() — stop이면 루프가 스스로 멈춘다
- 카파시 4원칙(폭주 방지 운영 원칙)을 코드 수준 브레이크로 구현:
    1) 작게 나눠 실행(단계 단위 실행·로그)   2) 사람이 승인(게이트)
    3) 항상 검증(체크리스트·검수 워커)        4) 예산으로 제한(가드)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import knot, usage
from .backends import BackendResult, run_backend
from .config import Config

SCORE_RE = re.compile(r"SCORE:\s*(\d+(?:\.\d+)?)")

# 할루시네이션 방지 지침 — 모든 워커 프롬프트에 주입.
ANTI_HALLUCINATION = (
    "[사실성 규칙] 추측을 사실처럼 쓰지 마라. 모르면 '모름'이라고 명시하라. "
    "존재하지 않는 파일·함수·API·플래그·라이브러리를 지어내지 마라. "
    "코드·경로·명령을 언급하면 실제 근거(존재 여부·출처)를 밝히고, 확신이 없으면 불확실하다고 표시하라.")

# yok3x 기법 — 코딩 작업(생산자)에 계획→구현→자가검증 구조를 강제.
YOK3X_TECHNIQUE = (
    "[yok3x 기법] 순서를 지켜라: (1) 계획 — 접근을 2~4줄로 먼저 요약. "
    "(2) 구현 — 계획대로 코드를 작성/수정. "
    "(3) 자가검증 — 끝에 'SELF-CHECK:'로 엣지케이스·오류처리·요구충족을 스스로 점검. "
    "한 번에 전부 완벽히 하려 말고 작게 나눠 진행하라.")

# 검수 워커용 — 환각/날조를 명시 지적하게 함.
REVIEW_GUARD = (
    "[검증 지침] 산출물의 모든 코드·사실 주장을 근거에 대조하라. "
    "지어낸 API·존재하지 않는 함수·검증 안 된 확신을 '환각'으로 명시 지적하라.")

# 근거 없는 과잉 확신 표현(가벼운 휴리스틱)
_OVERCONFIDENCE = ("반드시 동작", "무조건 동작", "100% 정확", "완벽하게 동작",
                   "definitely works", "guaranteed to work", "never fails")


@dataclass
class StepLog:
    index: int
    worker: str
    task_kind: str
    status: str          # done | failed | skipped | blocked
    summary: str = ""
    score: float | None = None
    checklist: list[str] = field(default_factory=list)


class RunAborted(Exception):
    pass


class Orchestrator:
    def __init__(self, cfg: Config, auto: bool | None = None,
                 ask: Callable[[str], str] | None = None):
        self.cfg = cfg
        self.auto = cfg.yok3x.get("auto_approve", False) if auto is None else auto
        self.ask = ask or (lambda msg: input(msg))
        # 마이크로초까지 포함 — 같은 초에 시작한 동시 런이 같은 run_dir를 공유해
        # 서로의 step 파일을 덮어써 손상시키던 충돌을 방지한다.
        self.run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        self.run_dir = cfg.paths.runs / self.run_id
        self.steps: list[StepLog] = []
        self._step_i = 0
        self.pattern = "-"
        self.task_desc = ""   # 상태/채팅 표시용 작업 목표
        # 태스크 옵션(코딩 기능): run_task_file이 세팅
        self.workdir: str | None = None      # 워커/검증 실행 디렉터리
        self.verify_cmd: str = ""            # 테스트/린트 게이트 명령
        self.verify_timeout: int = 300       # verify_cmd 제한시간(초) — task로 재정의 가능
        self.context_globs: list[str] = []   # 레포 컨텍스트 주입 glob
        self.rubric: str = ""                # 채점표 파일 경로

    # ------------------------------------------------------------ infra

    def _log(self, msg: str) -> None:
        print(msg, flush=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / "run.log").open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")

    def _isolated_cwd(self) -> str:
        """workdir 없는 워커용 빈 실행 디렉터리. claude/codex CLI는 실행 cwd의 git·파일
        컨텍스트를 자동 주입하는데, 레포 안에서 돌리면 워커가 프롬프트의 [작업] 대신
        레포 파일(brief.md·계획서 등)을 '진짜 작업'으로 오인해 헤맨다. 빈 dir에서 실행해
        차단한다. 런당 한 번 만들어 재사용."""
        d = getattr(self, "_iso_dir", None)
        if not d:
            import tempfile
            d = self._iso_dir = tempfile.mkdtemp(prefix="yok3x_iso_")
        return d

    def _save_status(self, state: str, extra: dict | None = None) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": self.run_id,
            "state": state,
            "pattern": self.pattern,
            "task": self.task_desc,
            "flavor": self.cfg.yok3x["flavor"],
            "updated": datetime.now().isoformat(timespec="seconds"),
            "steps": [s.__dict__ for s in self.steps],
        }
        if extra:
            data.update(extra)
        (self.run_dir / "status.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _gate(self, description: str) -> bool:
        """승인 게이트. False면 해당 단계 건너뜀, 'q'면 런 중단."""
        if self.auto:
            self._log(f"[gate] auto-approve: {description}")
            return True
        ans = self.ask(f"[gate] {description} — 진행? [y/N/q] ").strip().lower()
        if ans == "q":
            raise RunAborted("사용자 중단(q)")
        ok = ans == "y"
        self._log(f"[gate] {'승인' if ok else '거부'}: {description}")
        return ok

    # ------------------------------------------------------------ 코딩 기능

    def _repo_context(self) -> str:
        """context_globs 로 지정된 파일들을 프롬프트 주입 블록으로 만든다."""
        if not self.context_globs:
            return ""
        import glob as _glob
        base = Path(self.workdir) if self.workdir else Path(".")
        parts = []
        budget = int(self.cfg.yok3x.get("repo_context_max_chars", 6000))
        for pat in self.context_globs:
            for fp in sorted(_glob.glob(str(base / pat), recursive=True))[:20]:
                p = Path(fp)
                if not p.is_file():
                    continue
                try:
                    txt = p.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                snippet = knot.clip(txt, min(2000, budget))
                budget -= len(snippet)
                parts.append(f"--- {p.name} ---\n{snippet}")
                if budget <= 0:
                    break
            if budget <= 0:
                break
        return "[레포 컨텍스트]\n" + "\n\n".join(parts) if parts else ""

    def _rubric_text(self) -> str:
        if not self.rubric:
            return ""
        p = (Path(self.workdir) / self.rubric) if self.workdir else Path(self.rubric)
        if not p.exists():
            p = Path(self.rubric)
        if p.exists():
            return "[채점표 rubric]\n" + knot.clip(p.read_text(encoding="utf-8-sig", errors="replace"), 3000)
        return ""

    def _run_verify(self) -> tuple[bool, str]:
        """테스트/린트 게이트: verify_cmd 를 workdir에서 실제 실행(객관 검증)."""
        import shlex as _shlex
        import subprocess as _sp
        self._step_i += 1
        idx = self._step_i
        cmd = self.verify_cmd
        try:
            proc = _sp.run(cmd, shell=True, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=self.workdir or None, timeout=self.verify_timeout)
            ok = proc.returncode == 0
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        except _sp.TimeoutExpired:
            ok, out = False, f"verify timeout({self.verify_timeout}s)"
        except Exception as e:
            ok, out = False, f"verify 실행 실패: {type(e).__name__}: {e}"
        out = out[-2000:]
        self.steps.append(StepLog(idx, "verify", "verify", "done" if ok else "failed",
                                  summary=f"exit={'0' if ok else 'nonzero'}",
                                  checklist=[] if ok else ["검증 실패(테스트/린트 비정상 종료)"]))
        self._log(f"[verify] {'통과' if ok else '실패'}: {cmd}")
        return ok, out

    def _checklist(self, res: BackendResult) -> list[str]:
        """검증 체크리스트: 실패 항목만 기록."""
        issues = []
        if not res.ok:
            issues.append(f"실행 실패: {res.error[:200]}")
        if not res.text.strip():
            issues.append("빈 응답")
        if len(res.text) > 20000:
            issues.append("응답 과대(20k+ chars) — 컨텍스트 오염 위험")
        for marker in ("I cannot", "죄송하지만 할 수 없"):
            if marker in res.text[:200]:
                issues.append("거부성 응답 감지")
        # 할루시네이션 방지: 근거 없는 과잉 확신 표현 표시
        ah = self.cfg.yok3x.get("anti_hallucination", {})
        if ah.get("enabled", True) and ah.get("flag_unverified", True):
            for phrase in _OVERCONFIDENCE:
                if phrase in res.text:
                    issues.append(f"검증필요: 근거 없는 확신 표현('{phrase}')")
                    break
        return issues

    @staticmethod
    def _defect_sig(text: str) -> tuple[str, ...]:
        """리뷰어가 '실제로 지적한 결함'만 뽑아 정규화한 서명.

        스톨 판정의 근거. 응답의 메타 품질(빈 응답 등, _checklist)이 아니라 리뷰어가
        산출물에 대해 나열한 지적사항을 본다. SCORE 줄·글머리표·번호·구두점을 제거하고
        소문자·공백정규화한 뒤 정렬된 집합으로 만든다 → 같은 결함이 반복되면(수렴 실패)
        라운드 간 서명이 같아진다. 순서 바뀜과 가벼운 재서술에 견디도록 집합으로 비교.
        """
        issues = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.upper().startswith("SCORE"):
                continue
            line = re.sub(r"^[\-\*•·\d\.\)\(]+\s*", "", line)  # 글머리표/번호 제거
            line = re.sub(r"\s+", " ", line).strip().lower().strip(".,;:!?·")
            if len(line) >= 4:  # 짧은 잡음 조각 제외
                issues.append(line)
        return tuple(sorted(set(issues)))

    # ------------------------------------------------------------ worker call

    def call_worker(self, worker: str, task: str, task_kind: str = "general",
                    extra_context: str = "", cwd: str | None = None) -> BackendResult:
        self._step_i += 1
        idx = self._step_i
        cfg = self.cfg

        # 1) 요금 가드 — stop이면 루프가 스스로 멈춘다
        allowed, verdict = usage.guard_allows(cfg, worker)
        if verdict.level == "warn":
            self._log(f"[guard] 경고: {verdict.backend} {verdict.metric} {verdict.ratio:.0%} ({verdict.detail})")
        if not allowed:
            self.steps.append(StepLog(idx, worker, task_kind, "blocked",
                                      f"guard stop: {verdict.backend} {verdict.detail}"))
            self._save_status("stopped_by_guard")
            raise RunAborted(f"요금 가드 정지: {verdict.backend} {verdict.metric} "
                             f"{verdict.ratio:.0%} ({verdict.detail})")

        # 2) 승인 게이트
        if not self._gate(f"step {idx}: {worker} ← {task_kind} :: {task[:80]}"):
            self.steps.append(StepLog(idx, worker, task_kind, "skipped"))
            return BackendResult(backend="-", ok=False, error="skipped by gate")

        # 3) 프롬프트 조립: 역할 + brief/context(글자 제한) + knot 공유 기억
        w = cfg.worker(worker)
        parts = [f"[역할] {w['role']}"]
        # 할루시네이션 방지 + yok3x 기법 주입
        if cfg.yok3x.get("anti_hallucination", {}).get("enabled", True):
            parts.append(ANTI_HALLUCINATION)
        if cfg.yok3x.get("yok3x_technique", {}).get("enabled", True):
            if task_kind in ("critic", "review"):
                parts.append(REVIEW_GUARD)
            else:
                parts.append(YOK3X_TECHNIQUE)
        # workdir가 없으면 편집할 레포가 없다 → 파일 생성 대신 완성 코드를 본문에 직접.
        # role의 '파일 변경 보고' 지침을 명시적으로 무효화해야 claude가 '변경점 보고'가 아니라
        # 실제 코드를 출력한다. (workdir가 있으면 파일 편집+변경점 보고 워크플로우 유지)
        if not self.workdir and task_kind in ("build", "revise", "general"):
            parts.append("[중요·출력형식] 이 작업엔 작업 디렉터리가 없다. 위 역할의 "
                         "'파일 변경 보고' 지침은 적용하지 마라. 파일을 만들지 말고, "
                         "완성된 코드 전문을 코드블록으로 응답 본문에 직접 제시하라. "
                         "파일 경로·변경점 보고가 아니라 실제 코드를 출력하라.")
        brief = knot.read_brief(cfg).strip()
        if brief:
            parts.append(f"[brief.md]\n{knot.clip(brief, cfg.yok3x['brief_max_chars'])}")
        ctx = knot.read_context(cfg).strip()
        if ctx:
            parts.append(f"[context.md]\n{knot.clip(ctx, cfg.yok3x['context_max_chars'])}")
        mem = knot.context_for_prompt(cfg, task)
        if mem:
            parts.append(mem)
        if extra_context:
            parts.append(extra_context)
        parts.append(f"[작업]\n{task}")
        prompt = "\n\n".join(parts)

        # 3.5) 적응형 열화: 한도 인근이면 모델 다운그레이드(P1). 모든 열화는 명시 로깅.
        action, model_override = usage.degrade_plan(cfg, worker, verdict)
        if action == "downgrade" and model_override:
            self._log(f"[degrade] {worker} 사용률 {verdict.ratio:.0%} → 모델 다운그레이드: {model_override}")

        # 4) 실행 + 사용량 기록. workdir가 있으면 그 디렉터리에서, 없으면 빈 격리 dir에서
        # 실행한다(레포 컨텍스트가 워커를 오염시키는 것을 방지 — _isolated_cwd 참조).
        run_cwd = cwd or self.workdir or self._isolated_cwd()
        self._log(f"[run] step {idx} → {worker} ({w['backend']})")
        res = run_backend(w["backend"], cfg.backends[w["backend"]], prompt,
                          cwd=run_cwd, model=model_override)
        usage.record(cfg, worker, task_kind, res)

        # 5) 검증 체크리스트 + 파일 로그
        checklist = self._checklist(res)
        score = None
        m = SCORE_RE.search(res.text)
        if m:
            score = float(m.group(1))
        self.steps.append(StepLog(idx, worker, task_kind,
                                  "done" if res.ok else "failed",
                                  summary=res.text[:200], score=score,
                                  checklist=checklist))
        step_file = self.run_dir / f"step_{idx:02d}_{worker}.json"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        step_file.write_text(json.dumps({
            "worker": worker, "task_kind": task_kind, "task": task,
            "ok": res.ok, "error": res.error, "text": res.text,
            "score": score, "checklist": checklist,
            "usage": {"cost_usd": res.cost_usd, "total_tokens": res.total_tokens,
                      "duration_ms": res.duration_ms},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save_status("running")
        if checklist:
            self._log(f"[check] step {idx} 이슈: {'; '.join(checklist)}")
        return res

    # ------------------------------------------------------------ patterns

    def run_pipeline(self, task: str, stages: list[dict[str, str]]) -> None:
        """Pipeline: 이전 단계 출력이 다음 단계 입력이 된다."""
        self.pattern = "pipeline"
        self._save_status("running", {"task": task})
        prev = ""
        repo = self._repo_context()
        for i, st in enumerate(stages):
            t = st.get("task", task)
            blocks = []
            if i == 0 and repo:
                blocks.append(repo)
            if prev:
                blocks.append(f"[이전 단계 출력]\n{knot.clip(prev, 4000)}")
            res = self.call_worker(st["worker"], t, st.get("kind", "general"),
                                   "\n\n".join(blocks))
            if res.ok:
                prev = res.text
        if self.verify_cmd:
            ok, out = self._run_verify()
            prev += f"\n\n[검증 결과] exit={'0' if ok else 'nonzero'}\n{out[:800]}"
        self._finish(task, prev)

    def run_fanout(self, task: str, workers: list[str], join_worker: str | None = None) -> None:
        """Fan-out/Fan-in: 여러 워커에 같은 작업 → 결과 취합."""
        self.pattern = "fanout-fanin"
        self._save_status("running", {"task": task})
        outs = []
        for w in workers:
            res = self.call_worker(w, task, "fanout")
            if res.ok:
                outs.append(f"### {w}\n{res.text}")
        merged = "\n\n".join(outs)
        if join_worker and outs:
            res = self.call_worker(
                join_worker,
                "아래 여러 워커의 결과를 하나의 최종안으로 통합하라.",
                "fanin", extra_context=knot.clip(merged, 6000))
            merged = res.text if res.ok else merged
        self._finish(task, merged)

    def run_producer_reviewer(self, task: str, producer: str, reviewer: str,
                              max_rounds: int = 2, pass_score: float = 8.0) -> None:
        """Producer-Reviewer: 한 모델이 만들고 다른 모델이 채점(멀티 에이전트 검수).

        코딩 강화: 레포 컨텍스트 주입 · 테스트/검증 게이트(객관) · rubric · 스톨 감지.
        통과 조건 = SCORE >= pass_score **그리고** (verify_cmd 있으면) 검증 통과.
        """
        self.pattern = "producer-reviewer"
        self._save_status("running", {"task": task})
        artifact = ""
        repo, rubric = self._repo_context(), self._rubric_text()
        prev_sig = None
        for rnd in range(1, max_rounds + 1):
            t = task if rnd == 1 else f"{task}\n\n검수 지적을 반영해 수정하라."
            blocks = []
            if rnd == 1 and repo:
                blocks.append(repo)
            if artifact:
                blocks.append(f"[직전 산출물]\n{knot.clip(artifact, 4000)}")
            prod = self.call_worker(producer, t, "build" if rnd == 1 else "revise",
                                    "\n\n".join(blocks))
            if not prod.ok:
                break
            artifact = prod.text

            # 테스트/검증 게이트(객관): 통과 실패는 하드 신호
            verify_ok, verify_out = (True, "")
            if self.verify_cmd:
                verify_ok, verify_out = self._run_verify()

            rev_blocks = [f"[산출물]\n{knot.clip(artifact, 6000)}"]
            if rubric:
                rev_blocks.append(rubric)
            if self.verify_cmd:
                rev_blocks.append(f"[테스트/검증 결과] exit={'0(통과)' if verify_ok else 'nonzero(실패)'}\n{verify_out[:1200]}")
            rev = self.call_worker(
                reviewer,
                "다음 산출물을 채점하라. 첫 줄 'SCORE: <0-10>', 이후 결함과 수정 지시. "
                "테스트/검증 결과가 실패면 통과시키지 마라.",
                "critic", extra_context="\n\n".join(rev_blocks))
            score = self.steps[-1].score
            issues_sig = self._defect_sig(rev.text)
            self._log(f"[review] round {rnd} score={score} verify={'ok' if verify_ok else 'fail'}")

            passed = (score is not None and score >= pass_score) and verify_ok
            if passed:
                self._log(f"[review] 통과 기준({pass_score}) + 검증 충족 — 종료")
                break

            # 스톨 감지: 점수 + 리뷰어가 지적한 결함이 직전 라운드와 동일하면
            # 수렴 실패로 조기 종료(리뷰어가 같은 결함을 되풀이 = 생산자가 못 고침).
            sig = (score, issues_sig)
            if prev_sig is not None and sig == prev_sig:
                self._log("[stall] 같은 점수·결함 반복 — 수렴 실패로 조기 종료")
                knot.save(self.cfg, f"stall-{self.run_id}",
                          f"작업: {task}\n스톨 조기종료(round {rnd}, score {score}).\n"
                          f"반복 결함: {list(issues_sig)}",
                          tags=["stall", "run"], source="orchestrator")
                break
            prev_sig = sig
            artifact += f"\n\n<!-- 검수 r{rnd} -->\n{rev.text}" if rev.ok else ""
        self._finish(task, artifact)

    # ------------------------------------------------------------ finish

    def _finish(self, task: str, final_output: str) -> None:
        out = self.run_dir / "final_output.md"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(final_output or "(출력 없음)", encoding="utf-8")
        # brief.md 갱신(글자 제한) + knot에 런 요약 저장 → 에이전트 간 기억 공유
        knot.write_brief(self.cfg, f"# brief.md\n\n최근 런 {self.run_id}\n작업: {task}\n"
                                   f"결과 요약: {final_output[:400]}")
        knot.save(self.cfg, f"run-{self.run_id}",
                  f"작업: {task}\n\n결과:\n{final_output[:1500]}",
                  tags=["run", self.cfg.yok3x["flavor"]], source="orchestrator")
        self._save_status("done")
        self._log(f"[done] 최종 산출물: {out}")


# ---------------------------------------------------------------- loop

def run_task_file(cfg: Config, task_file: str | Path, auto: bool | None = None,
                  ask=None) -> str:
    """task.json 실행. 반환: 종료 상태 문자열."""
    spec = json.loads(Path(task_file).read_text(encoding="utf-8-sig"))  # BOM 방어
    orch = Orchestrator(cfg, auto=auto, ask=ask)
    # 코딩 태스크 옵션. task가 workdir를 지정하면 우선, 없으면 전역 workspace를 상속.
    orch.workdir = spec.get("workdir") or cfg.yok3x.get("workspace") or None
    if orch.workdir and not Path(orch.workdir).is_dir():
        msg = f"workdir 없음(오타?): {orch.workdir}"
        print(f"[error] {msg}")
        orch._save_status("aborted", {"reason": msg})
        return f"aborted: {msg}"
    # task가 지정하면 우선, 없으면 yok3x.json 전역 기본값을 상속(프로젝트 전체 게이트).
    orch.verify_cmd = spec.get("verify_cmd") or cfg.yok3x.get("verify_cmd", "") or ""
    orch.verify_timeout = int(spec.get("verify_timeout_sec")
                              or cfg.yok3x.get("verify_timeout_sec", 300))
    orch.context_globs = spec.get("context_globs", []) or []
    orch.rubric = spec.get("rubric", "") or ""
    pattern = spec.get("pattern", "producer-reviewer")
    task = spec["task"]
    orch.task_desc = task
    try:
        if pattern == "pipeline":
            orch.run_pipeline(task, spec["stages"])
        elif pattern in ("fanout", "fanout-fanin"):
            orch.run_fanout(task, spec["workers"], spec.get("join_worker"))
        elif pattern == "producer-reviewer":
            orch.run_producer_reviewer(task, spec.get("producer", "claude-main"),
                                       spec.get("reviewer", "codex-critic"),
                                       int(spec.get("max_rounds", 2)),
                                       float(spec.get("pass_score", 8.0)))
        else:
            raise ValueError(f"unknown pattern: {pattern}")
        return "done"
    except RunAborted as e:
        orch._log(f"[stop] {e}")
        orch._save_status("aborted", {"reason": str(e)})
        return f"aborted: {e}"


def run_loop(cfg: Config, task_file: str | Path, iterations: int = 3,
             sleep_sec: float = 1.0, auto: bool = True) -> None:
    """에이전트 루프: 가드가 stop을 내리면 루프가 스스로 멈춘다."""
    for i in range(1, iterations + 1):
        print(f"\n===== loop {i}/{iterations} =====")
        state = run_task_file(cfg, task_file, auto=auto)
        if state.startswith("aborted"):
            print(f"[loop] 가드/사용자 정지로 루프 종료: {state}")
            break
        time.sleep(sleep_sec)
