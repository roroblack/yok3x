"""오케스트레이터.

- flavor별 orchestrator/worker 구조 (harness.json의 flavors)
- 워크플로우 패턴: pipeline / fanout-fanin / producer-reviewer
- 승인 게이트: 각 단계 실행 전 y/n (auto_approve로 생략 가능)
- 파일 기반 로그: .harness/runs/<run_id>/status.json + step_NN_<worker>.json
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
        self.auto = cfg.harness.get("auto_approve", False) if auto is None else auto
        self.ask = ask or (lambda msg: input(msg))
        self.run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.run_dir = cfg.paths.runs / self.run_id
        self.steps: list[StepLog] = []
        self._step_i = 0
        self.pattern = "-"

    # ------------------------------------------------------------ infra

    def _log(self, msg: str) -> None:
        print(msg, flush=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / "run.log").open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")

    def _save_status(self, state: str, extra: dict | None = None) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": self.run_id,
            "state": state,
            "pattern": self.pattern,
            "flavor": self.cfg.harness["flavor"],
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
        return issues

    # ------------------------------------------------------------ worker call

    def call_worker(self, worker: str, task: str, task_kind: str = "general",
                    extra_context: str = "") -> BackendResult:
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
        brief = knot.read_brief(cfg).strip()
        if brief:
            parts.append(f"[brief.md]\n{knot.clip(brief, cfg.harness['brief_max_chars'])}")
        ctx = knot.read_context(cfg).strip()
        if ctx:
            parts.append(f"[context.md]\n{knot.clip(ctx, cfg.harness['context_max_chars'])}")
        mem = knot.context_for_prompt(cfg, task)
        if mem:
            parts.append(mem)
        if extra_context:
            parts.append(extra_context)
        parts.append(f"[작업]\n{task}")
        prompt = "\n\n".join(parts)

        # 4) 실행 + 사용량 기록
        self._log(f"[run] step {idx} → {worker} ({w['backend']})")
        res = run_backend(w["backend"], cfg.backends[w["backend"]], prompt)
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
        for st in stages:
            t = st.get("task", task)
            extra = f"[이전 단계 출력]\n{knot.clip(prev, 4000)}" if prev else ""
            res = self.call_worker(st["worker"], t, st.get("kind", "general"), extra)
            if res.ok:
                prev = res.text
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
        """Producer-Reviewer: 한 모델이 만들고 다른 모델이 채점(멀티 에이전트 검수)."""
        self.pattern = "producer-reviewer"
        self._save_status("running", {"task": task})
        artifact = ""
        for rnd in range(1, max_rounds + 1):
            t = task if rnd == 1 else f"{task}\n\n검수 지적을 반영해 수정하라."
            extra = f"[직전 산출물]\n{knot.clip(artifact, 4000)}" if artifact else ""
            prod = self.call_worker(producer, t, "build" if rnd == 1 else "revise", extra)
            if not prod.ok:
                break
            artifact = prod.text
            rev = self.call_worker(
                reviewer,
                "다음 산출물을 채점하라. 첫 줄 'SCORE: <0-10>', 이후 결함과 수정 지시.",
                "critic", extra_context=f"[산출물]\n{knot.clip(artifact, 6000)}")
            score = self.steps[-1].score
            self._log(f"[review] round {rnd} score={score}")
            if score is not None and score >= pass_score:
                self._log(f"[review] 통과 기준({pass_score}) 충족 — 종료")
                break
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
                  tags=["run", self.cfg.harness["flavor"]], source="orchestrator")
        self._save_status("done")
        self._log(f"[done] 최종 산출물: {out}")


# ---------------------------------------------------------------- loop

def run_task_file(cfg: Config, task_file: str | Path, auto: bool | None = None,
                  ask=None) -> str:
    """task.json 실행. 반환: 종료 상태 문자열."""
    spec = json.loads(Path(task_file).read_text(encoding="utf-8-sig"))  # BOM 방어
    orch = Orchestrator(cfg, auto=auto, ask=ask)
    pattern = spec.get("pattern", "producer-reviewer")
    task = spec["task"]
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
