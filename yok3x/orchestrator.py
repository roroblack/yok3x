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
import hashlib
import math
import os
import re
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import acquire, artifacts, calibration, knot, reserve, usage
from .backends import BackendResult, run_backend, terminate_process
from .config import Config
from ._version import __version__

SCORE_RE = re.compile(r"SCORE:\s*(\d+(?:\.\d+)?)")

# 할루시네이션 방지 지침 — 모든 워커 프롬프트에 주입.
ANTI_HALLUCINATION = (
    "[사실성 규칙] 추측을 사실처럼 쓰지 마라. 모르면 '모름'이라고 명시하라. "
    "존재하지 않는 파일·함수·API·플래그·라이브러리를 지어내지 마라. "
    "코드·경로·명령을 언급하면 실제 근거(존재 여부·출처)를 밝히고, 확신이 없으면 불확실하다고 표시하라.")


def verify_evidence(qa_items: list[dict], workdir: str | Path | None) -> list[dict]:
    """QA evidence의 경로와 심볼을 호스트 파일시스템에서 기계 확인한다."""
    base = Path(workdir) if workdir is not None else Path.cwd()
    checks: list[dict] = []
    for qa_item in qa_items:
        answer = qa_item.get("answer")
        evidence_items = (answer.get("evidence") if isinstance(answer, dict)
                          else qa_item.get("evidence")) or []
        path_results: list[bool] = []
        symbol_results: list[bool] = []
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                path_results.append(False)
                continue
            relative_path = str(evidence.get("path") or "").strip()
            evidence_path = base / relative_path
            path_exists = bool(relative_path) and evidence_path.is_file()
            path_results.append(path_exists)
            symbol = str(evidence.get("symbol") or "").strip()
            if path_exists and symbol:
                text = evidence_path.read_text(encoding="utf-8", errors="replace")
                symbol_results.append(symbol in text)

        path_exists = bool(path_results) and all(path_results)
        symbol_found = all(symbol_results) if symbol_results else None
        checks.append({
            "claim_id": acquire.core_claim(qa_item)["claim_id"],
            "evidence_check": {
                "path_exists": path_exists,
                "symbol_found": symbol_found,
            },
        })
    return checks

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

# 적대적 검수(ARIS AD1) — 리뷰어를 '채점'이 아니라 '반증/파괴'에 맞춘다.
ADVERSARIAL_REVIEW = (
    "다음 산출물을 적대적으로 검수하라. 너의 목표는 통과시키는 것이 아니라 '무너뜨리는 것'이다. "
    "가장 강한 반례·미검증 가정·엣지케이스 실패·보안/정확성 결함을 적극적으로 찾아라. 근거 없이 "
    "'동작한다'고 주장된 부분을 지목하고 반증 가능한 구체적 시나리오를 제시하라. 테스트/검증 결과가 "
    "실패면 통과시키지 마라. 첫 줄에 'SCORE: <0-10>'(엄격), 이후 치명 결함부터 나열하고 재현·수정 "
    "지시를 써라. 확신이 없으면 낮은 점수를 줘라.")

# 근거 없는 과잉 확신 표현(가벼운 휴리스틱)
_OVERCONFIDENCE = ("반드시 동작", "무조건 동작", "100% 정확", "완벽하게 동작",
                   "definitely works", "guaranteed to work", "never fails")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """같은 디렉터리의 임시 파일을 완성한 뒤 JSON 파일을 원자적으로 교체한다."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


@dataclass
class CallSpec:
    worker: str
    task: str
    task_kind: str = "general"
    extra_context: str = ""
    cwd: str | None = None
    read_only: bool = False
    # 준비 단계에서 결정되는 값. 가드 폴오버·열화는 실행 시점 상태를 따른다.
    backend: str = ""
    model: str | None = None
    prompt: str = ""
    run_cwd: str = ""
    index: int | None = None
    route_reason: str = ""
    # 제어 스레드에서 배치 승인을 끝낸 spec만 True. worker에서 input()을 부르지 않게 한다.
    batch_approved: bool = False

    @property
    def call_key(self) -> str:
        """호출 내용만으로 만드는 재생 키. 단계 번호는 의도적으로 포함하지 않는다."""
        raw = "|".join((
            self.worker, self.task_kind, self.backend, self.model or "",
            self.prompt, "true" if self.read_only else "false",
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class StepLog:
    index: int
    worker: str
    task_kind: str
    status: str          # done | failed | skipped | blocked
    summary: str = ""
    score: float | None = None
    checklist: list[str] = field(default_factory=list)
    # 관찰가능성(A-lite): 스텝별 계측. None=측정불가(백엔드 미보고), 0=실제 0(무료/미과금)을 구분.
    tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    replayed: bool = False


class RunAborted(Exception):
    def __init__(self, message: str, results: list[BackendResult | None] | None = None,
                 *, cause: str = "unknown"):
        super().__init__(message)
        # 병렬 중단 때 이미 완료된 입력 순서 결과를 호출자가 회수할 수 있게 한다.
        self.results = results
        self.cause = cause


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
        # 병렬 워커가 공유하는 런 상태·파일 기록은 한 임계구역에서 직렬화한다.
        # _log/_save_status가 중첩 호출될 수 있어 재진입 락을 쓴다.
        self._state_lock = threading.RLock()
        self._active_process_lock = threading.Lock()
        self._active_processes: set[Any] = set()
        self._parallel_local = threading.local()
        self._failover_map: dict[str, str] = {}   # P2: 이번 런에서 폴오버한 워커→대체 backend(sticky)
        self._failovers = 0                        # P2: 이번 런 전환 횟수(상한 체크)
        self.pattern = "-"
        self.task_desc = ""   # 상태/채팅 표시용 작업 목표
        self.label = ""       # 작업 그룹 라벨(콘솔 작업별 뷰). 비면 GUI가 무제목 처리
        # 작업별 에이전트 배치. 전역 cfg는 건드리지 않고 이 런에서만 부분 병합한다.
        self.agents_override: dict[str, dict[str, Any]] = {}
        # 태스크 옵션(코딩 기능): run_task_file이 세팅
        self.workdir: str | None = None      # 워커/검증 실행 디렉터리
        self.verify_cmd: str = ""            # 테스트/린트 게이트 명령
        self.verify_timeout: int = 300       # verify_cmd 제한시간(초) — task로 재정의 가능
        self.context_globs: list[str] = []   # 레포 컨텍스트 주입 glob
        self.rubric: str = ""                # 채점표 파일 경로
        self.adversarial: bool = cfg.yok3x.get("adversarial_review", False)  # ARIS AD1 적대적 검수
        self.escalate: dict = {}   # 조건부 라우팅: 낮은 점수 지속 시 워커 전환(task spec의 escalate)
        # 산출물 게시(opt-in). 워커는 파일을 못 쓰므로(텍스트 생산자) 오케스트레이터가 대신 쓴다.
        # {"enabled":bool, "root":str|None, "overwrite":bool} — root 없으면 workdir/yok3x-out/<run_id>
        self.materialize: dict = {}
        # 심판 캘리브레이션(F0): 루프가 최종 score·verify_ok·rounds를 여기 남기면 _finish가 기록.
        self._calib: dict = {}
        # G-1: 순차 pipeline 재개에서만 채워지는 성공 prefix 캐시.
        self._replay_cache: dict[str, dict[str, Any]] = {}
        self.resume_from: str | None = None

    # ------------------------------------------------------------ infra

    def _worker(self, name: str) -> dict[str, Any]:
        """전역 워커 설정의 복사본에 이번 런의 작업별 배치만 부분 병합한다."""
        worker = dict(self.cfg.worker(name))
        worker.update(self.agents_override.get(name, {}))
        return worker

    def _log(self, msg: str) -> None:
        with self._state_lock:
            print(msg, flush=True)
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / "run.log").open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")

    def _isolated_cwd_path(self) -> str:
        """파일을 만들지 않고 이번 런의 격리 실행 경로만 결정한다."""
        return getattr(
            self, "_iso_dir",
            str(Path(tempfile.gettempdir()) / f"yok3x_iso_{self.run_id}"))

    def _isolated_cwd(self) -> str:
        """workdir 없는 워커용 빈 실행 디렉터리. claude/codex CLI는 실행 cwd의 git·파일
        컨텍스트를 자동 주입하는데, 레포 안에서 돌리면 워커가 프롬프트의 [작업] 대신
        레포 파일(brief.md·계획서 등)을 '진짜 작업'으로 오인해 헤맨다. 빈 dir에서 실행해
        차단한다. 런당 한 번 만들어 재사용."""
        d = self._isolated_cwd_path()
        Path(d).mkdir(parents=True, exist_ok=True)
        self._iso_dir = d
        return d

    def _save_status(self, state: str, extra: dict | None = None) -> None:
        with self._state_lock:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "run_id": self.run_id,
                "state": state,
                "pattern": self.pattern,
                "task": self.task_desc,
                "label": self.label,
                "flavor": self.cfg.yok3x["flavor"],
                "updated": datetime.now().isoformat(timespec="seconds"),
                "steps": [s.__dict__ for s in self.steps],
            }
            if self.resume_from:
                data["resume_from"] = self.resume_from
            if extra:
                data.update(extra)
            _atomic_write_json(self.run_dir / "status.json", data)

    def _register_process(self, proc: Any, abort_event: threading.Event) -> None:
        """병렬 CLI 프로세스를 등록하고 중단과 경합해 늦게 뜬 프로세스도 즉시 종료한다."""
        with self._active_process_lock:
            self._active_processes.add(proc)
        if abort_event.is_set():
            terminate_process(proc)

    def _unregister_process(self, proc: Any) -> None:
        with self._active_process_lock:
            self._active_processes.discard(proc)

    def _terminate_active_processes(self) -> None:
        """Future.cancel()로 멈출 수 없는 실행 중 CLI 프로세스 트리를 실제 종료한다."""
        with self._active_process_lock:
            active = list(self._active_processes)
        for proc in active:
            terminate_process(proc)

    def _gate(self, description: str) -> bool:
        """승인 게이트. False면 해당 단계 건너뜀, 'q'면 런 중단."""
        if self.auto:
            self._log(f"[gate] auto-approve: {description}")
            return True
        ans = self.ask(f"[gate] {description} — 진행? [y/N/q] ").strip().lower()
        if ans == "q":
            raise RunAborted("사용자 중단(q)", cause="user_abort")
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

    def estimate_call(self, spec: CallSpec) -> tuple[int, float]:
        """프롬프트 길이 기반의 거친 토큰/비용 상한 추정.

        실제 사용량이 아니며 모델별 tokenizer/가격을 정확히 흉내 내지 않는다. 조절값은
        guard.reservation 설정에 있고 실제치는 실행 뒤 usage.record가 기록한다.
        """
        rcfg = ((self.cfg.yok3x.get("guard") or {}).get("reservation") or {})
        chars_per_token = float(rcfg.get("chars_per_token", 2.0))
        output_ratio = float(rcfg.get("output_token_ratio", 2.0))
        usd_per_1k = float(rcfg.get("usd_per_1k_tokens", 0.03))
        if chars_per_token <= 0 or output_ratio < 0 or usd_per_1k < 0:
            raise ValueError("guard.reservation 추정 설정은 유효한 0 이상 값이어야 합니다")
        input_est = max(1, math.ceil(len(spec.prompt) / chars_per_token))
        est_tokens = max(1, math.ceil(input_est * (1.0 + output_ratio)))
        return est_tokens, est_tokens * usd_per_1k / 1000.0

    def _batch_description(self, specs: list[CallSpec]) -> str:
        rows = [
            f"  - worker={spec.worker} · backend={spec.backend} · task_kind={spec.task_kind}"
            for spec in specs
        ]
        estimates = [self.estimate_call(spec) for spec in specs]
        tokens = sum(item[0] for item in estimates)
        usd = sum(item[1] for item in estimates)
        rows.append(
            f"  예상 상한: calls={len(specs)}(정확) · tokens≈{tokens:,}(추정) · USD≈${usd:.4f}(추정)")
        return "batch\n" + "\n".join(rows)

    def approve_batch(self, specs: list[CallSpec]) -> bool:
        """배치 실행 전에 제어 스레드에서 한 번만 승인한다."""
        description = self._batch_description(specs)
        if self.auto:
            self._log(f"[gate] auto-approve: {description}")
            for spec in specs:
                spec.batch_approved = True
            return True
        ans = self.ask(f"[gate] {description}\n진행? [y/N/q] ").strip().lower()
        if ans == "q":
            raise RunAborted("사용자 중단(q)", cause="user_abort")
        ok = ans == "y"
        self._log(f"[gate] {'승인' if ok else '거부'}: {description}")
        if ok:
            for spec in specs:
                spec.batch_approved = True
        return ok

    def reserve_and_approve(self, specs: list[CallSpec]) -> bool:
        """C-3 병렬 실행 전에 stale 정리→예약→배치 승인을 모두 끝낸다."""
        rcfg = ((self.cfg.yok3x.get("guard") or {}).get("reservation") or {})
        cleaned = reserve.cleanup_stale(
            self.cfg, ttl=float(rcfg.get("pending_ttl_sec", 1800)))
        if cleaned:
            self._log(f"[reserve] stale pending {cleaned}개 정리")
        estimates = [self.estimate_call(spec) for spec in specs]
        est_tokens = sum(item[0] for item in estimates)
        est_usd = sum(item[1] for item in estimates)
        if not reserve.reserve(self.cfg, self.run_id, len(specs), est_tokens, est_usd):
            self._log(
                f"[reserve] 실패: calls={len(specs)}(정확) · "
                f"tokens≈{est_tokens:,}(추정) · USD≈${est_usd:.4f}(추정)")
            return False
        try:
            if not self.approve_batch(specs):
                reserve.release(self.cfg, self.run_id)
                return False
        except BaseException:
            reserve.release(self.cfg, self.run_id)
            raise
        return True

    def prepare_call(self, worker: str, task: str, task_kind: str = "general",
                     extra_context: str = "", cwd: str | None = None,
                     read_only: bool = False) -> CallSpec:
        """라우팅·프롬프트·실행 경로를 부작용 없이 미리 확정한다."""
        cfg = self.cfg
        w = self._worker(worker)

        # 유효 backend·model 결정. 프로파일 라우팅 뒤 sticky 폴오버를 적용한다.
        # 요금 가드에 따른 폴오버·열화는 시점 의존 상태이므로 execute_call에 남긴다.
        backend, model_override = w["backend"], (w.get("model") or None)
        rb, rm, route_reason = resolve_model(cfg, task_kind,
                                             available=lambda b: usage.backend_available(cfg, b))
        if rb and rb in cfg.backends:
            backend, model_override = rb, rm
        else:
            route_reason = ""
        _sticky = self._failover_map.get(worker)
        if _sticky and _sticky in cfg.backends:
            backend, model_override = _sticky, None

        # 프롬프트 조립. 코드생성 워커(build/revise/general)는 [작업]을 '맨 앞'에 두고
        # '지금 구현·되묻지 마라'를 명시한다 — 헤드리스 claude가 역할 설명을 '작업 없음'으로
        # 오인해 명확화만 되묻는 실패모드(체계적)를 막기 위함. critic/review는 산출물
        # (extra_context) 뒤에 채점 지시를 두는 기존 순서 유지.
        is_codegen = task_kind in ("build", "revise", "general")
        parts: list[str] = []
        if is_codegen:
            # 코드생성: [작업]을 맨 앞 + 계획/자가검증/사실성/출력형식을 '한 블록'으로 압축.
            # 장황한 역할·anti-halluc·기법 블록은 헤드리스 claude를 '파일 편집 시도(→권한 대기)'
            # 나 '작업 없음 되묻기'로 몰아 체계적으로 실패시켰다(실측). 미니멀 프롬프트가 1턴에
            # 안정적으로 코드를 낸다(6s vs 실패).
            parts.append(f"[작업]\n{task}")
            parts.append("[지시] 완성된 코드를 코드블록으로 즉시 출력하라. 파일을 만들거나 편집하려 "
                         "하지 말고 코드는 텍스트로만 답한다. 코드 앞에 접근을 2~3줄로 요약(계획)하고, "
                         "끝에 'SELF-CHECK:'로 엣지케이스·오류처리·요구충족을 점검하라. 존재하지 않는 "
                         "API·파일을 지어내지 말고, 명확화를 되묻지 말고 합리적 가정으로 곧장 구현하라.")
            # 산출물 게시(opt-in)가 켜졌을 때만 파일 경로 명시 계약을 준다. 워커는 여전히 파일을
            # 쓰지 않는다 — 경로를 '선언'만 하고, 실제 쓰기는 오케스트레이터가 검증 후 수행한다.
            if (self.materialize or {}).get("enabled"):
                parts.append(artifacts.FILES_CONTRACT)
        else:
            parts.append(f"[역할] {w['role']}")
            # 리뷰/크리틱도 텍스트 산출자다. 코드생성과 동일하게 '파일을 만들거나 편집하려 하지
            # 말고 텍스트로만 답하라'를 명시 — 없으면 헤드리스 워커가 '파일 생성(→쓰기 권한 대기)'로
            # 새어 실질 산출 없이 "권한 필요"만 반복하는 실패모드가 난다(실측: is_palindrome 런).
            parts.append("[출력] 리뷰·수정 제안은 텍스트로만 답하라. 파일을 만들거나 편집하려 하지 "
                         "말고, 쓰기 권한을 기다리지 마라. 코드가 필요하면 코드블록으로 제시하라.")
            if cfg.yok3x.get("anti_hallucination", {}).get("enabled", True):
                parts.append(ANTI_HALLUCINATION)
            if cfg.yok3x.get("yok3x_technique", {}).get("enabled", True):
                parts.append(REVIEW_GUARD)
        # 실제 내용이 있는 brief/context/memory만 주입 — 스캐폴드 플레이스홀더는 노이즈라 제외.
        brief = knot.read_brief(cfg).strip()
        if brief and "글자 제한 적용)" not in brief:
            parts.append(f"[brief.md]\n{knot.clip(brief, cfg.yok3x['brief_max_chars'])}")
        ctx = knot.read_context(cfg).strip()
        if ctx and "글자 제한 적용)" not in ctx:
            parts.append(f"[context.md]\n{knot.clip(ctx, cfg.yok3x['context_max_chars'])}")
        mem = knot.context_for_prompt(cfg, task)
        if mem:
            parts.append(mem)
        if extra_context:
            parts.append(extra_context)
        if not is_codegen:
            parts.append(f"[작업]\n{task}")
        prompt = "\n\n".join(parts)

        run_cwd = cwd or self.workdir or self._isolated_cwd_path()
        return CallSpec(
            worker=worker, task=task, task_kind=task_kind,
            extra_context=extra_context, cwd=cwd, read_only=read_only,
            backend=backend, model=model_override, prompt=prompt,
            run_cwd=run_cwd, route_reason=route_reason)

    def execute_call(self, spec: CallSpec) -> BackendResult:
        """준비된 호출에 단계번호·가드·승인·실행·기록을 적용한다."""
        with self._state_lock:
            if spec.index is None:
                self._step_i += 1
                spec.index = self._step_i
            else:
                # 병렬 경로는 제어 스레드에서 index를 선할당한다.
                self._step_i = max(self._step_i, spec.index)
            idx = spec.index
        cfg = self.cfg
        worker, task, task_kind = spec.worker, spec.task, spec.task_kind
        backend, model_override = spec.backend, spec.model
        w = self._worker(worker)
        call_key = spec.call_key

        # 성공 prefix 재생은 가드·승인·backend보다 먼저 처리한다. 실제 호출도 과금도 없다.
        cached = self._replay_cache.get(call_key)
        if cached is not None:
            cached_usage = cached["usage"]
            res = BackendResult(
                backend=backend, ok=True, text=cached["text"], error=cached["error"],
                cost_usd=float(cached_usage["cost_usd"] or 0),
                total_tokens=int(cached_usage["total_tokens"] or 0),
                duration_ms=int(cached_usage["duration_ms"] or 0),
                meta={"replayed": True, "source_step": cached["_source_index"]},
            )
            with self._state_lock:
                self.steps.append(StepLog(
                    idx, worker, task_kind, "done", summary=res.text[:200],
                    score=cached["score"], checklist=list(cached["checklist"]),
                    tokens=(res.total_tokens or None), cost_usd=(res.cost_usd or None),
                    duration_ms=res.duration_ms, replayed=True))
                self.run_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(self.run_dir / f"step_{idx:02d}_{worker}.json", {
                    "worker": worker, "task_kind": task_kind, "task": task,
                    "backend": backend, "model": model_override,
                    "read_only": spec.read_only, "call_key": call_key,
                    "ok": True, "error": res.error, "text": res.text,
                    "score": cached["score"], "checklist": list(cached["checklist"]),
                    "usage": dict(cached_usage), "replayed": True,
                })
                self._save_status("running")
            self._log(
                f"[resume] step {idx} 재생: {worker} "
                f"(source step {cached['_source_index']}, call_key={call_key})")
            return res

        if spec.route_reason:
            self._log(
                f"[route] {task_kind} → {spec.route_reason} "
                f"({backend}{'/' + model_override if model_override else ''})")

        # 요금 가드 + P2 백엔드 폴오버(on/off, 기본 off). off면 stop→루프 정지(현행 동작).
        # on이면 failover_ratio↑/stop에서 여유 있는 다른 도구로 전환(런당 상한·sticky 히스테리시스).
        verdict = usage.check_backend(cfg, backend)
        if verdict.level == "warn":
            self._log(f"[guard] 경고: {verdict.backend} {verdict.metric} {verdict.ratio:.0%} ({verdict.detail})")
        _deg = (cfg.yok3x.get("guard") or {}).get("degrade") or {}
        if verdict.level == "stop" or verdict.ratio >= float(_deg.get("failover_ratio", 0.97)):
            alt = usage.failover_backend(cfg, worker, backend, self._failovers)
            if alt:
                with self._state_lock:
                    self._log(f"[failover] {backend} {verdict.ratio:.0%} 한도 → {alt}로 전환(이번 런 유지)")
                    self._failover_map[worker] = alt
                    self._failovers += 1
                backend, model_override, verdict = alt, None, usage.check_backend(cfg, alt)
            elif verdict.level == "stop":
                with self._state_lock:
                    self.steps.append(StepLog(idx, worker, task_kind, "blocked",
                                              f"guard stop: {verdict.backend} {verdict.detail}"))
                    self._save_status("stopped_by_guard")
                raise RunAborted(f"요금 가드 정지: {verdict.backend} {verdict.metric} "
                                 f"{verdict.ratio:.0%} ({verdict.detail})",
                                 cause="guard_stop")

        # 승인 게이트
        if (not spec.batch_approved
                and not self._gate(f"step {idx}: {worker} ← {task_kind} :: {task[:80]}")):
            with self._state_lock:
                self.steps.append(StepLog(idx, worker, task_kind, "skipped"))
            return BackendResult(backend="-", ok=False, error="skipped by gate")

        # 적응형 열화 P1(최종 backend·verdict 기준). 라우팅/폴오버 후 backend의 lite로 낮춤.
        action, lite = usage.degrade_plan(cfg, worker, verdict, backend=backend)
        if action == "downgrade" and lite:
            model_override = lite
            self._log(f"[degrade] {worker} 사용률 {verdict.ratio:.0%} → 모델 다운그레이드: {lite}")

        # 실행 + 사용량 기록. workdir가 있으면 그 디렉터리에서, 없으면 빈 격리 dir에서
        # 실행한다(레포 컨텍스트가 워커를 오염시키는 것을 방지 — _isolated_cwd 참조).
        run_cwd = spec.run_cwd
        if run_cwd == self._isolated_cwd_path():
            self._isolated_cwd()
        # 추론 강도(effort): 워커별 지정 > 전역 기본 default_effort. backend가 effort_arg를 지원할 때만
        # 실제 전달(claude/codex). 폴오버로 backend가 바뀌면 그 backend의 effort_arg 유무에 따름.
        effort = w.get("effort") or cfg.yok3x.get("default_effort") or None
        self._log(f"[run] step {idx} → {worker} ({backend}{'·' + effort if effort else ''})")
        backend_kwargs = {"cwd": run_cwd, "model": model_override, "effort": effort}
        if spec.read_only:
            backend_kwargs["read_only"] = True
        abort_event = getattr(self._parallel_local, "abort_event", None)
        if abort_event is not None:
            # 병렬 경로에서만 Popen 핸들을 노출한다. 단일 호출은 기존 subprocess.run 계약 유지.
            backend_kwargs.update(
                process_started=lambda proc: self._register_process(proc, abort_event),
                process_finished=self._unregister_process,
                cancel_event=abort_event,
            )
        res = run_backend(backend, cfg.backends[backend], spec.prompt, **backend_kwargs)

        # 5) 검증 체크리스트 + 파일 로그
        checklist = self._checklist(res)
        score = None
        m = SCORE_RE.search(res.text)
        if m:
            score = float(m.group(1))
        with self._state_lock:
            usage.record(cfg, worker, task_kind, res)
            self.steps.append(StepLog(idx, worker, task_kind,
                                      "done" if res.ok else "failed",
                                      summary=res.text[:200], score=score,
                                      checklist=checklist,
                                      # duration은 항상 측정(서브프로세스 계측). tokens/cost는 백엔드가
                                      # 보고할 때만(0/누락은 None=미측정으로 둬 GUI가 '—'로 정직 표시).
                                      tokens=(res.total_tokens or None),
                                      cost_usd=(res.cost_usd or None),
                                      duration_ms=res.duration_ms))
            step_file = self.run_dir / f"step_{idx:02d}_{worker}.json"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(step_file, {
                "worker": worker, "task_kind": task_kind, "task": task,
                "backend": backend, "model": model_override,
                "read_only": spec.read_only, "call_key": call_key,
                "ok": res.ok, "error": res.error, "text": res.text,
                "score": score, "checklist": checklist,
                "usage": {"cost_usd": res.cost_usd, "total_tokens": res.total_tokens,
                          "duration_ms": res.duration_ms},
                "replayed": False,
            })
            self._save_status("running")
        if checklist:
            self._log(f"[check] step {idx} 이슈: {'; '.join(checklist)}")
        return res

    def call_workers_parallel(self, specs: list[CallSpec]) -> list[BackendResult | None]:
        """준비된 호출을 예약·배치승인 뒤 backend 상한을 지켜 병렬 실행한다.

        반환 슬롯은 입력 순서를 유지하며 일반 예외 슬롯만 ``None``이 된다. 가드 중단은
        실행 중 CLI 프로세스를 종료한 뒤 ``RunAborted``로 전파하고, 이미 완료된 결과는
        예외의 ``results``에 같은 입력 순서로 보존한다.
        """
        if not specs:
            return []

        results: list[BackendResult | None] = [None] * len(specs)
        # 예약 실패/승인 거부면 worker를 하나도 만들지 않는다.
        if not self.reserve_and_approve(specs):
            return results

        abort_event = threading.Event()
        executor: ThreadPoolExecutor | None = None
        futures: dict[Future[BackendResult | None], int] = {}
        try:
            parallel_cfg = ((self.cfg.yok3x.get("guard") or {}).get("parallel") or {})
            if not parallel_cfg.get("enabled", False):
                # 기본 비활성: 기존 execute_call을 입력 순서대로 호출하는 정확한 순차 폴백.
                for position, spec in enumerate(specs):
                    results[position] = self.execute_call(spec)
                return results

            max_workers = max(1, int(parallel_cfg.get("max_workers", 4)))
            max_per_backend = max(1, int(parallel_cfg.get("max_per_backend", 2)))

            # worker가 _step_i를 경쟁하지 않도록 제어 스레드에서 연속 index를 확정한다.
            with self._state_lock:
                for spec in specs:
                    self._step_i += 1
                    spec.index = self._step_i

            semaphores = {
                backend: threading.Semaphore(max_per_backend)
                for backend in {spec.backend for spec in specs}
            }

            def run_one(spec: CallSpec) -> BackendResult | None:
                if abort_event.is_set():
                    return None
                semaphore = semaphores[spec.backend]
                with semaphore:
                    # semaphore 대기 중 중단됐으면 새 backend/CLI 호출을 시작하지 않는다.
                    if abort_event.is_set():
                        return None
                    self._parallel_local.abort_event = abort_event
                    try:
                        return self.execute_call(spec)
                    finally:
                        try:
                            del self._parallel_local.abort_event
                        except AttributeError:
                            pass

            executor = ThreadPoolExecutor(max_workers=max_workers,
                                          thread_name_prefix="yok3x-worker")
            for position, spec in enumerate(specs):
                futures[executor.submit(run_one, spec)] = position

            pending = set(futures)
            aborted: RunAborted | None = None
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    position = futures[future]
                    if future.cancelled():
                        continue
                    try:
                        results[position] = future.result()
                    except RunAborted as exc:
                        if aborted is None:
                            aborted = exc
                            abort_event.set()
                            for other in pending:
                                other.cancel()
                            self._terminate_active_processes()
                    except Exception as exc:
                        # all-settled: 한 호출의 예외가 다른 성공 결과를 지우지 않는다.
                        self._log(
                            f"[parallel] 실패: step {specs[position].index} · "
                            f"{type(exc).__name__}: {exc}")

            if aborted is not None:
                aborted.results = list(results)
                raise aborted
            return results
        finally:
            if abort_event.is_set():
                self._terminate_active_processes()
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            with self._state_lock:
                self.steps.sort(key=lambda step: step.index)
            reserve.release(self.cfg, self.run_id)

    def call_worker(self, worker: str, task: str, task_kind: str = "general",
                    extra_context: str = "", cwd: str | None = None,
                    read_only: bool = False) -> BackendResult:
        return self.execute_call(self.prepare_call(
            worker, task, task_kind, extra_context, cwd, read_only))

    # ------------------------------------------------------------ ACQUIRE preflight

    def _save_acquire(self, qa_items: list[dict], questions: list[dict],
                      dropped: list[dict] | None = None) -> None:
        """이슈별 QA를 knot가 아닌 현재 run의 일시 메모리에만 저장한다."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": self.run_id,
            "questions": questions,
            "qa_items": qa_items,
            "dropped": dropped or [],
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        _atomic_write_json(self.run_dir / "acquire.json", data)
        self._log(f"[acquire] 저장: 질문 {len(questions)}개, 유효 QA {len(qa_items)}개")

    def acquire_preflight(self, issue: str, questioner: str, answerers: list[str],
                          qa_count: int = 2, workdir: str | None = None
                          ) -> tuple[str, list[dict]]:
        """수리 전에 필요한 저장소 지식을 독립 QA로 수집한다.

        실패하거나 비용 가드가 멈춘 경우 본 수리를 막지 않고 빈 컨텍스트를 반환한다.
        """
        try:
            count = max(0, int(qa_count))
        except (TypeError, ValueError):
            self._log(f"[acquire] 잘못된 qa_count({qa_count!r}) — preflight 생략")
            return "", []
        if (not isinstance(questioner, str) or not questioner.strip()
                or not isinstance(answerers, (list, tuple))
                or not answerers
                or not all(isinstance(worker, str) and worker.strip() for worker in answerers)
                or count == 0):
            self._log("[acquire] questioner/answerers/qa_count 부족 — preflight 생략")
            return "", []
        answerers = list(answerers)

        # ref: ACQUIRE(Know-Before-Fix), 이슈 해결 전 QA 지식 수집 — 비용 가드가
        # stop이면 조사보다 본 수리를 우선한다. 중복 워커는 한 번만 확인한다.
        for worker in dict.fromkeys([questioner, *answerers]):
            try:
                backend = self._worker(worker)["backend"]
                verdict = usage.check_backend(self.cfg, backend)
            except (KeyError, TypeError, ValueError) as exc:
                self._log(f"[acquire] 워커/가드 확인 실패({worker}): {exc} — preflight 생략")
                return "", []
            if verdict.level == "stop":
                self._log(f"[acquire] 가드 stop({worker}/{backend}) — preflight 생략, 본 수리 우선")
                return "", []

        self._log(f"[acquire] Questioner 시작: {questioner}, 목표 질문 {count}개")
        try:
            question_result = self.call_worker(
                questioner, acquire.build_questioner_prompt(issue, count),
                task_kind="general", cwd=workdir)
        except RunAborted:
            raise
        except Exception as exc:
            self._log(f"[acquire] Questioner 호출 실패: {type(exc).__name__}: {exc}")
            return "", []
        if not question_result.ok:
            self._log(f"[acquire] Questioner 실패: {question_result.error or '응답 없음'}")
            return "", []
        questions = acquire.parse_questions(question_result.text, count)
        if not questions:
            self._log("[acquire] 질문 파싱 결과 0개 — preflight 종료")
            return "", []
        self._log(f"[acquire] 질문 {len(questions)}개 파싱 완료")

        prepared: list[tuple[int, dict, CallSpec]] = []
        for index, question in enumerate(questions):
            answerer = answerers[index % len(answerers)]
            self._log(f"[acquire] Answerer {index + 1}/{len(questions)} 시작: {answerer}")
            try:
                spec = self.prepare_call(
                    answerer, acquire.build_answerer_prompt(issue, question),
                    task_kind="general", cwd=workdir, read_only=True)
            except Exception as exc:
                self._log(
                    f"[acquire] Answerer 준비 실패({answerer}): "
                    f"{type(exc).__name__}: {exc}")
                continue
            prepared.append((index, question, spec))
        specs = [spec for _, _, spec in prepared]

        try:
            results = self.call_workers_parallel(specs)
        except RunAborted:
            raise
        except Exception as exc:
            self._log(f"[acquire] Answerer 배치 호출 실패: {type(exc).__name__}: {exc}")
            results = [None] * len(specs)

        qa_items: list[dict] = []
        for (index, question, spec), answer_result in zip(prepared, results):
            answerer = spec.worker
            if answer_result is None:
                self._log(f"[acquire] Answerer 실패 슬롯({answerer}) — QA {index + 1} 제외")
                continue
            if not answer_result.ok:
                self._log(f"[acquire] Answerer 실패({answerer}): "
                          f"{answer_result.error or '응답 없음'}")
                continue
            answer = acquire.parse_answer(answer_result.text)
            valid, reason = acquire.validate_answer(answer) if answer is not None else (False, "JSON 파싱 실패")
            if not valid:
                self._log(f"[acquire] QA {index + 1} 제외: {reason}")
                continue
            qa_items.append({"question": question, "answer": answer})
            self._log(f"[acquire] QA {index + 1} 검증 통과")

        if not qa_items:
            self._save_acquire(qa_items, questions)
            self._log("[acquire] 유효 QA 0개 — 빈 컨텍스트로 본 수리 계속")
            return "", []

        kept = qa_items
        dropped: list[dict] = []
        try:
            checks = verify_evidence(qa_items, workdir or self.workdir)
            if len(checks) == len(qa_items) and checks:
                kept, dropped = acquire.apply_verdicts(qa_items, checks)
                confirmed = sum(item.get("verdict") == "confirmed" for item in kept)
                partial = sum(item.get("verdict") == "partial" for item in kept)
                self._log(
                    f"[acquire] 재검증: confirmed {confirmed} · partial {partial} · "
                    f"contradicted {len(dropped)}(폐기)")
            else:
                self._log("[acquire] 재검증 결과 없음 — 기존 QA로 본 수리 계속")
        except Exception as exc:
            # 재검증은 보조 안전장치다. 파일 I/O 실패가 본 수리를 막아서는 안 된다.
            self._log(f"[acquire] 재검증 실패: {type(exc).__name__}: {exc} — 기존 QA로 본 수리 계속")

        self._save_acquire(kept, questions, dropped)
        if not kept:
            self._log("[acquire] 재검증 통과 QA 0개 — 빈 컨텍스트로 본 수리 계속")
            return "", []
        context = acquire.render_qa_context(issue, kept)
        self._log(f"[acquire] preflight 완료: 유효 QA {len(kept)}개")
        return context, kept

    # ------------------------------------------------------------ patterns

    def run_pipeline(self, task: str, stages: list[dict[str, str]],
                     initial_context: str = "") -> None:
        """Pipeline: 이전 단계 출력이 다음 단계 입력이 된다."""
        self.pattern = "pipeline"
        self._save_status("running", {"task": task})
        prev = ""
        repo = self._repo_context()
        for i, st in enumerate(stages):
            t = st.get("task", task)
            blocks = []
            if i == 0 and initial_context:
                blocks.append(initial_context)
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

    def run_fanout(self, task: str, workers: list[str], join_worker: str | None = None,
                   initial_context: str = "") -> None:
        """Fan-out/Fan-in: 여러 워커에 같은 작업 → 결과 취합."""
        self.pattern = "fanout-fanin"
        self._save_status("running", {"task": task})
        outs = []
        specs = []
        for index, w in enumerate(workers):
            # fanout에는 별도 build kind가 없으므로 첫 진입 워커에만 정적 QA를 1회 주입한다.
            specs.append(self.prepare_call(
                w, task, "fanout",
                extra_context=initial_context if index == 0 else ""))
        results = self.call_workers_parallel(specs)
        for w, res in zip(workers, results):
            # 병렬 완료 순서와 무관하게 워커 입력 순서로 취합하고 실패 슬롯은 건너뛴다.
            if res is not None and res.ok:
                outs.append(f"### {w}\n{res.text}")
        merged = "\n\n".join(outs)
        if join_worker and outs:
            res = self.call_worker(
                join_worker,
                "아래 여러 워커의 결과를 하나의 최종안으로 통합하라.",
                "fanin", extra_context=knot.clip(merged, 6000))
            merged = res.text if res.ok else merged
        self._finish(task, merged)

    def _ensure_cross_family(self, producer: str, reviewer: str) -> str:
        """적대적 검수(ARIS): 프로듀서와 리뷰어가 같은 모델 패밀리면 다른 패밀리 워커로 리뷰어
        교체(교차검증 강화). 다른 패밀리 워커가 없으면 경고만. 반환: (교체된) reviewer."""
        pb = (self._worker(producer) or {}).get("backend")
        rb = (self._worker(reviewer) or {}).get("backend")
        if not pb or pb != rb:
            return reviewer
        for w in self.cfg.yok3x.get("workers", {}):
            wb = (self._worker(w) or {}).get("backend")
            if wb and wb != pb:
                self._log(f"[adversarial] 교차 패밀리: 리뷰어 {reviewer}({rb}) → {w}({wb}) 교체")
                return w
        self._log(f"[adversarial] 경고: 프로듀서·리뷰어 같은 패밀리({pb}), 다른 패밀리 워커 없음")
        return reviewer

    def run_producer_reviewer(self, task: str, producer: str, reviewer: str,
                              max_rounds: int = 2, pass_score: float = 8.0,
                              initial_context: str = "") -> None:
        """Producer-Reviewer: 한 모델이 만들고 다른 모델이 채점(멀티 에이전트 검수).

        코딩 강화: 레포 컨텍스트 주입 · 테스트/검증 게이트(객관) · rubric · 스톨 감지.
        통과 조건 = SCORE >= pass_score **그리고** (verify_cmd 있으면) 검증 통과.
        adversarial=True면 리뷰어가 '반증/파괴' 우선 + 교차 패밀리 강제(ARIS AD1).
        """
        self.pattern = "producer-reviewer"
        if self.adversarial:
            reviewer = self._ensure_cross_family(producer, reviewer)
            self._log("[adversarial] 적대적 검수 모드 — 리뷰어가 반증 우선")
        self._save_status("running", {"task": task})
        # 조건부 라우팅(에스컬레이션) 대상 사전검증 — 부재/오타는 조용히 폴백 말고 명확히 실패(codex 리뷰).
        escalated = False
        for role in ("to_producer", "to_reviewer"):
            w = self.escalate.get(role)
            if w and w not in self.cfg.yok3x.get("workers", {}):
                raise RunAborted(f"escalate.{role} 없는 워커: {w}", cause="config_error")
        artifact = ""
        repo, rubric = self._repo_context(), self._rubric_text()
        prev_sig = None
        for rnd in range(1, max_rounds + 1):
            t = task if rnd == 1 else f"{task}\n\n검수 지적을 반영해 수정하라."
            blocks = []
            if rnd == 1 and initial_context:
                blocks.append(initial_context)
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
            review_instr = ADVERSARIAL_REVIEW if self.adversarial else (
                "다음 산출물을 채점하라. 첫 줄 'SCORE: <0-10>', 이후 결함과 수정 지시. "
                "테스트/검증 결과가 실패면 통과시키지 마라.")
            rev = self.call_worker(reviewer, review_instr, "critic",
                                   extra_context="\n\n".join(rev_blocks))
            score = self.steps[-1].score
            issues_sig = self._defect_sig(rev.text)
            self._log(f"[review] round {rnd} score={score} verify={'ok' if verify_ok else 'fail'}")
            # 캘리브레이션 라벨: verify_cmd가 있어야 지상진실. 없으면 label 없음(상관 제외). 최종 라운드 값이 남음.
            self._calib = {"score": score, "rounds": rnd,
                           "verify_ok": (bool(verify_ok) if self.verify_cmd else None),
                           "backend": (self._worker(producer) or {}).get("backend"),
                           "effort": (self._worker(producer) or {}).get("effort") or None}

            passed = (score is not None and score >= pass_score) and verify_ok
            if passed:
                self._log(f"[review] 통과 기준({pass_score}) + 검증 충족 — 종료")
                break

            # 조건부 라우팅(에스컬레이션, LangGraph 조건부엣지 이식): 낮은 점수가 지속되면 다음
            # 라운드부터 워커를 1회 전환한다. 통과 아닐 때만, prev_sig 초기화(워커가 바뀌면 스톨
            # 비교가 무효 — codex 리뷰 반영). 대상은 시작 전 검증(부재 시 spec 오류로 실패).
            esc = self.escalate
            if (esc and not escalated and rnd >= int(esc.get("after_round", 2))
                    and score is not None and score < float(esc.get("if_score_below", 6))):
                if esc.get("to_producer"):
                    producer = esc["to_producer"]
                if esc.get("to_reviewer"):
                    reviewer = (self._ensure_cross_family(producer, esc["to_reviewer"])
                                if self.adversarial else esc["to_reviewer"])
                escalated = True
                prev_sig = None            # 워커 전환 → 스톨 시그니처 초기화(오탐 방지)
                artifact += f"\n\n<!-- 검수 r{rnd} -->\n{rev.text}" if rev.ok else ""
                self._log(f"[escalate] round {rnd} score={score} → producer={producer}, reviewer={reviewer}")
                continue                   # 새 워커로 다음 라운드(이번 라운드 스톨 판정 건너뜀)

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

    def _materialize_root(self) -> Path:
        """게시 루트. 기본은 workdir/yok3x-out/<run_id> — workdir 직접 저장은 기존 프로젝트와
        충돌할 수 있어 런별로 격리한다(codex 권고)."""
        raw = (self.materialize or {}).get("root")
        if raw:
            return Path(raw).expanduser()
        base = Path(self.workdir) if self.workdir else self.run_dir
        return base / "yok3x-out" / self.run_id

    def _materialize_outputs(self, final_output: str) -> dict:
        """final_output의 `​```file:<path>` 블록을 검증해 실제 파일로 게시한다.

        워커 권한은 그대로 두고(텍스트 생산자) **오케스트레이터가 대신 쓴다**. 텍스트 생성 성공과
        파일 게시 성공은 별개 상태다 — 실패해도 런을 깨지 않고 사유를 남긴다(codex 권고).
        """
        conf = self.materialize or {}
        if not conf.get("enabled"):
            return {"enabled": False}
        root = self._materialize_root()
        blocks = artifacts.parse_file_blocks(final_output or "")
        if not blocks:
            self._log("[out] 게시할 파일 없음 — 워커가 ```file:<경로> 블록을 내지 않았다")
            return {"enabled": True, "ok": False, "reason": "file 블록 없음",
                    "root": str(root), "written": [], "rejected": []}
        existing: set[str] = set()
        if root.exists():
            existing = {str(p.relative_to(root)).replace("\\", "/")
                        for p in root.rglob("*") if p.is_file()}
        plan = artifacts.plan_files(blocks, existing=existing,
                                    overwrite=bool(conf.get("overwrite")),
                                    max_files=int(conf.get("max_files", 20)))
        written: list[dict] = []
        try:
            root.mkdir(parents=True, exist_ok=True)
            for fb in plan.accepted:
                dest = (root / fb.path)
                # 문자열 검증(artifacts)만 믿지 않고 **해석된 실제 경로**가 루트 안인지 재확인한다.
                # 심볼릭 링크로 루트 밖을 가리키는 경우를 여기서 막는다.
                try:
                    real_root = root.resolve()
                    real_dest = dest.resolve()
                    real_dest.relative_to(real_root)
                except (OSError, ValueError):
                    plan.rejected.append({"path": fb.path, "reason": "해석된 경로가 루트 밖(심볼릭 등)"})
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(f".{dest.name}.yok3x.tmp")
                tmp.write_text(fb.content, encoding="utf-8")
                os.replace(tmp, dest)        # 부분 저장 방지: 완성 후 원자적 교체
                written.append({"path": fb.path, "bytes": len(fb.content.encode("utf-8")),
                                "sha256": fb.sha256()})
        except OSError as e:
            self._log(f"[out] 게시 실패: {type(e).__name__}: {e}")
            return {"enabled": True, "ok": False, "reason": f"{type(e).__name__}: {e}",
                    "root": str(root), "written": written, "rejected": plan.rejected}
        for r in plan.rejected:
            self._log(f"[out] 거부: {r['path']} — {r['reason']}")
        if written:
            self._log(f"[out] 게시 {len(written)}개 → {root}")
        return {"enabled": True, "ok": bool(written), "root": str(root),
                "written": written, "rejected": plan.rejected}

    def _finish(self, task: str, final_output: str) -> None:
        out = self.run_dir / "final_output.md"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(final_output or "(출력 없음)", encoding="utf-8")
        # 산출물 게시(opt-in) — 텍스트 성공과 별개 상태로 기록해 "완성했다는데 파일이 없다"를 없앤다.
        try:
            mat = self._materialize_outputs(final_output or "")
        except Exception as e:                       # 게시 실패가 런을 깨지 않게
            self._log(f"[out] 게시 예외: {type(e).__name__}: {e}")
            mat = {"enabled": True, "ok": False, "reason": f"{type(e).__name__}: {e}"}
        # 주의: 여기서 바로 _save_status 하지 않는다 — 아래 최종 _save_status가 덮어써 materialized가
        # 유실된다(E2E에서 발견). 최종 저장에 함께 실어 한 번만 기록한다.
        # 주의: brief.md에 런 '출력'을 덮어쓰지 않는다. 과거엔 그렇게 했다가, 다음 런 프롬프트에
        # brief.md가 주입돼 워커가 직전 실패 출력("빈 작업입니다")을 그대로 따라하는 자기오염
        # 피드백 루프가 생겼다. brief.md는 사용자 작업 컨텍스트 전용(수동)으로 둔다.
        # Mem0식 요점 저장: 결론 신호(SELF-CHECK·SCORE·결정 등)만 응축해 knot에 이력으로 저장.
        # source="orchestrator" 런 노트는 이력·검색용이며 프롬프트에는 주입하지 않는다(context_for_prompt).
        key_points = knot.extract_key_points(final_output)
        knot.save(self.cfg, f"run-{self.run_id}",
                  f"작업: {task}\n\n요점:\n{key_points[:1200]}",
                  tags=["run", self.cfg.yok3x["flavor"]], source="orchestrator")
        self._save_status("done", {"materialized": mat} if mat.get("enabled") else None)
        self._log_calibration()
        self._log(f"[done] 최종 산출물: {out}")

    def _log_calibration(self) -> None:
        """심판 캘리브레이션 레코드를 .yok3x/calibration.jsonl에 append(F0 데이터 수집).
        선택 편향 주의(codex): 고른 경로 결과만 관측 — 초기엔 상관 확인용. 실패해도 런 안 깨짐."""
        try:
            c = self._calib
            if not c:
                return                          # producer-reviewer 아닌 패턴은 score/verify 없음 → 스킵
            rec = calibration.make_record(
                run_id=self.run_id, ts=datetime.now().isoformat(timespec="seconds"),
                pattern=self.pattern, backend=c.get("backend"), effort=c.get("effort"),
                rounds=c.get("rounds"), score=c.get("score"), verify_ok=c.get("verify_ok"),
                tokens=sum(int(s.tokens or 0) for s in self.steps if not s.replayed) or None,
                cost_usd=round(sum(float(s.cost_usd or 0) for s in self.steps
                                   if not s.replayed), 4) or None,
                duration_ms=sum(int(s.duration_ms or 0) for s in self.steps
                                if not s.replayed) or None,
                issues=sum(len(s.checklist or []) for s in self.steps))
            path = self.cfg.paths.runs.parent / "calibration.jsonl"   # .yok3x/calibration.jsonl
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:                  # 계측 실패가 런을 깨지 않게
            self._log(f"[calib] 기록 실패: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- loop

def resolve_model(cfg: Config, task_kind: str, available=None,
                  profile: str | None = None) -> tuple[str | None, str | None, str]:
    """상황별 모델 프로파일 라우팅. 반환 (backend|None, model_id|None, reason).

    S1: active_profile의 상황별 픽. active_profile이 비었거나 매핑/카탈로그가 없으면
        (None, None, "") = 오버라이드 없음(현행: 워커 기본 backend·CLI 기본 모델).
    S2: available(backend)->bool 콜러블이 주어지면 '가용한(설치+한도여유) 첫 후보'로 폴백.
        후보 순서 = 프로파일 픽 → 해당 상황 benchmarks 점수 내림차순(중복 제외). 폴백 시
        reason에 '(폴백)' 표기. 순수 함수(available 주입) — 결정적으로 테스트된다.
    사용자 우선: 프로파일은 '기본 추천'이며 call_worker에서 태스크 명시값이 있으면 이긴다.
    """
    yk = cfg.yok3x
    prof_name = (profile if profile is not None else yk.get("active_profile") or "").strip()
    if not prof_name:
        return (None, None, "")
    prof = (yk.get("profiles") or {}).get(prof_name)
    if not prof:
        return (None, None, "")
    situation = (yk.get("situations") or {}).get(task_kind, task_kind)
    bench_sit = (yk.get("benchmarks") or {}).get(situation) or {}
    if prof.get("_derive"):     # S3: benchmarks 최고점 모델 자동 채택(argmax), 없으면 "*"
        pick = max(bench_sit, key=lambda k: bench_sit[k]) if bench_sit else prof.get("*")
    else:
        pick = prof.get(situation) or prof.get("*")
    catalog = yk.get("models_catalog") or {}
    candidates: list[str] = [pick] if pick else []
    if available:   # S2: benchmarks 점수 내림차순으로 폴백 후보 확장
        for m in sorted(bench_sit, key=lambda k: bench_sit[k], reverse=True):
            if m not in candidates:
                candidates.append(m)
    for logical in candidates:
        entry = catalog.get(logical) or {}
        backend = entry.get("backend")
        if not backend:
            continue
        if available and not available(backend):
            continue
        reason = f"{prof_name}/{situation}→{logical}" + ("(폴백)" if logical != pick else "")
        return (backend, entry.get("model") or None, reason)
    return (None, None, "")


def _resume_supported(spec: dict[str, Any], cfg: Config) -> tuple[bool, str]:
    """G-1은 acquire/materialize 없는 순차 pipeline만 허용한다."""
    if spec.get("pattern", "producer-reviewer") != "pipeline":
        return False, "재개는 pattern=pipeline에서만 지원합니다"
    parallel = ((cfg.yok3x.get("guard") or {}).get("parallel") or {})
    if parallel.get("enabled", False):
        return False, "재개는 guard.parallel.enabled=false인 순차 pipeline에서만 지원합니다"
    for key in ("acquire", "materialize"):
        if key in spec:
            return False, f"재개는 {key}가 없는 pipeline에서만 지원합니다"
    return True, ""


def _manifest_workers(orch: Orchestrator, spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """task가 실제로 참조하는 worker의 실행 식별 필드만 고정한다."""
    pattern = spec.get("pattern", "producer-reviewer")
    names: list[str] = []
    if pattern == "pipeline":
        names.extend(stage.get("worker", "") for stage in spec.get("stages", []))
    elif pattern in ("fanout", "fanout-fanin"):
        names.extend(spec.get("workers", []) or [])
        names.append(spec.get("join_worker") or "")
    elif pattern == "producer-reviewer":
        names.extend((spec.get("producer", "claude-main"),
                      spec.get("reviewer", "codex-critic")))
        escalate = spec.get("escalate") or {}
        names.extend((escalate.get("to_producer") or "",
                      escalate.get("to_reviewer") or ""))
    acquire_spec = spec.get("acquire")
    if isinstance(acquire_spec, dict):
        names.append(acquire_spec.get("questioner") or "")
        names.extend(acquire_spec.get("answerers", []) or [])

    workers: dict[str, dict[str, Any]] = {}
    for name in sorted(set(filter(None, names))):
        worker = orch._worker(name)
        workers[name] = {
            "backend": worker.get("backend"),
            "model": worker.get("model") or None,
            "effort": worker.get("effort") or orch.cfg.yok3x.get("default_effort") or None,
        }
    return workers


def _make_manifest(orch: Orchestrator, spec: dict[str, Any], spec_bytes: bytes) -> dict[str, Any]:
    return {
        "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "pattern": spec.get("pattern", "producer-reviewer"),
        "workers": _manifest_workers(orch, spec),
        "flavor": orch.cfg.yok3x["flavor"],
        "verify_cmd": orch.verify_cmd,
        "yok3x_version": __version__,
    }


def _manifest_difference(old: Any, new: Any, path: str = "manifest") -> str | None:
    """strict manifest 불일치의 첫 위치를 사람이 읽을 수 있게 돌려준다."""
    if isinstance(old, dict) and isinstance(new, dict):
        if set(old) != set(new):
            missing = sorted(set(old) - set(new))
            added = sorted(set(new) - set(old))
            return f"{path} 필드 불일치(missing={missing}, added={added})"
        for key in sorted(old):
            diff = _manifest_difference(old[key], new[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if old != new:
        return f"{path} 불일치(이전={old!r}, 현재={new!r})"
    return None


_STEP_FILE_RE = re.compile(r"^step_(\d+)_.*\.json$")
_REPLAY_REQUIRED = {
    "worker", "task_kind", "task", "call_key", "ok", "error", "text",
    "score", "checklist", "usage",
}
_REPLAY_USAGE_REQUIRED = {"cost_usd", "total_tokens", "duration_ms"}


def _load_replay_prefix(run_dir: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """번호가 연속된 ok step만 읽는다. 손상/실패를 만나는 즉시 안전하게 중단한다."""
    indexed: dict[int, list[Path]] = {}
    for path in run_dir.glob("step_*.json"):
        match = _STEP_FILE_RE.match(path.name)
        if not match:
            continue
        index = int(match.group(1))
        indexed.setdefault(index, []).append(path)

    cache: dict[str, dict[str, Any]] = {}
    expected = 1
    for index in sorted(indexed):
        if index != expected:
            return cache, f"step {expected} 파일 없음"
        paths = indexed[index]
        if len(paths) != 1:
            return cache, f"step {index} 파일 중복"
        path = paths[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return cache, f"step {index} JSON 손상({type(exc).__name__})"
        if not isinstance(data, dict) or not _REPLAY_REQUIRED.issubset(data):
            return cache, f"step {index} 필수 필드 없음"
        usage_data = data.get("usage")
        if (not isinstance(usage_data, dict)
                or not _REPLAY_USAGE_REQUIRED.issubset(usage_data)):
            return cache, f"step {index} usage 필수 필드 없음"
        if data.get("ok") is not True:
            return cache, f"step {index} 성공 아님"
        call_key = data.get("call_key")
        if not isinstance(call_key, str) or not call_key:
            return cache, f"step {index} call_key 오류"
        cached = dict(data)
        cached["_source_index"] = index
        cache[call_key] = cached
        expected += 1
    return cache, "성공 prefix 끝"


def _run_task_file(cfg: Config, task_file: str | Path, auto: bool | None = None,
                   ask=None, *, resume_dir: Path | None = None
                   ) -> str | dict[str, str]:
    """task.json 실행. 반환: 종료 상태 문자열."""
    task_path = Path(task_file)
    spec_bytes = task_path.read_bytes()
    spec = json.loads(spec_bytes.decode("utf-8-sig"))  # BOM 방어
    orch = Orchestrator(cfg, auto=auto, ask=ask)
    orch.agents_override = spec.get("agents") or {}
    # 산출물 게시(opt-in). "이 폴더에 X 만들어줘"는 그 폴더 하위 신규 파일 생성에 대한 작업단위
    # 승인으로 본다(codex 권고) — 파일마다 다시 묻지 않는다. 단 덮어쓰기는 명시해야 한다.
    orch.materialize = spec.get("materialize") or {}
    # 작업 그룹 라벨(콘솔 작업별 뷰용): label 키가 있으면 그 값(빈값 허용=무제목),
    # 키 자체가 없으면(등록된 task 파일) 파일명으로 폴백.
    _lbl = spec.get("label")
    orch.label = (str(_lbl).strip() if _lbl is not None else Path(task_file).stem.strip())
    # 코딩 태스크 옵션. task가 workdir를 지정하면 우선, 없으면 전역 workspace를 상속.
    orch.workdir = spec.get("workdir") or cfg.yok3x.get("workspace") or None
    pattern = spec.get("pattern", "producer-reviewer")
    task = spec["task"]
    orch.pattern = pattern
    orch.task_desc = task
    if resume_dir is not None:
        orch.resume_from = resume_dir.name

    if orch.workdir and not Path(orch.workdir).is_dir():
        msg = f"workdir 없음(오타?): {orch.workdir}"
        print(f"[error] {msg}")
        orch._save_status("aborted", {
            "reason": msg, "cause": "config_error", "resumable": False})
        return f"aborted: {msg}"
    # task가 지정하면 우선, 없으면 yok3x.json 전역 기본값을 상속(프로젝트 전체 게이트).
    orch.verify_cmd = spec.get("verify_cmd") or cfg.yok3x.get("verify_cmd", "") or ""
    orch.verify_timeout = int(spec.get("verify_timeout_sec")
                              or cfg.yok3x.get("verify_timeout_sec", 300))
    orch.context_globs = spec.get("context_globs", []) or []
    orch.rubric = spec.get("rubric", "") or ""
    if "adversarial" in spec:                       # task가 명시하면 우선, 없으면 config 기본
        orch.adversarial = bool(spec.get("adversarial"))
    orch.escalate = spec.get("escalate") or {}      # 조건부 라우팅(에스컬레이션) 규칙
    manifest = _make_manifest(orch, spec, spec_bytes)
    if resume_dir is not None:
        supported, reason = _resume_supported(spec, cfg)
        if not supported:
            orch._save_status("aborted", {
                "reason": reason, "cause": "config_error", "resumable": False})
            return {"error": reason}
        try:
            previous_manifest = json.loads(
                (resume_dir / "manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            reason = f"재개 거부: 이전 manifest.json을 읽을 수 없습니다({type(exc).__name__})"
            orch._save_status("aborted", {
                "reason": reason, "cause": "config_error", "resumable": False})
            return {"error": reason}
        difference = _manifest_difference(previous_manifest, manifest)
        if difference:
            reason = f"재개 거부: strict manifest {difference}"
            orch._save_status("aborted", {
                "reason": reason, "cause": "config_error", "resumable": False})
            return {"error": reason}
        orch._replay_cache, prefix_reason = _load_replay_prefix(resume_dir)
        orch._log(f"[resume] {resume_dir.name}: 성공 prefix {len(orch._replay_cache)}개 "
                  f"적재 ({prefix_reason})")
    orch.run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(orch.run_dir / "manifest.json", manifest)
    try:
        acquire_context = ""
        acquire_spec = spec.get("acquire")
        if "acquire" in spec:
            if not isinstance(acquire_spec, dict):
                orch._log("[acquire] task spec 형식 오류 — preflight 생략")
            else:
                acquire_context, _ = orch.acquire_preflight(
                    task, acquire_spec.get("questioner", ""),
                    acquire_spec.get("answerers", []) or [],
                    acquire_spec.get("qa_count", 2), orch.workdir)
        if pattern == "pipeline":
            orch.run_pipeline(task, spec["stages"], initial_context=acquire_context)
        elif pattern in ("fanout", "fanout-fanin"):
            orch.run_fanout(task, spec["workers"], spec.get("join_worker"),
                            initial_context=acquire_context)
        elif pattern == "producer-reviewer":
            orch.run_producer_reviewer(task, spec.get("producer", "claude-main"),
                                       spec.get("reviewer", "codex-critic"),
                                       int(spec.get("max_rounds", 2)),
                                       float(spec.get("pass_score", 8.0)),
                                       initial_context=acquire_context)
        else:
            raise ValueError(f"unknown pattern: {pattern}")
        return "done"
    except RunAborted as e:
        orch._log(f"[stop] {e}")
        supported, _ = _resume_supported(spec, cfg)
        cause = getattr(e, "cause", "unknown")
        resumable = bool(supported and cause in ("guard_stop", "user_abort"))
        orch._save_status("aborted", {
            "reason": str(e), "cause": cause, "resumable": resumable})
        return f"aborted: {e}"


def run_task_file(cfg: Config, task_file: str | Path, auto: bool | None = None,
                  ask=None, resume_run_id: str | None = None
                  ) -> str | dict[str, str]:
    """task.json 실행. G-1 재개는 이전 lineage 잠금을 잡은 순차 pipeline만 허용한다."""
    if resume_run_id is None:
        return _run_task_file(cfg, task_file, auto=auto, ask=ask)
    if not isinstance(resume_run_id, str) or not resume_run_id.strip():
        return {"error": "재개 거부: resume_run_id가 비어 있습니다"}
    runs_root = cfg.paths.runs.resolve()
    resume_dir = (runs_root / resume_run_id).resolve()
    if resume_dir.parent != runs_root or not resume_dir.is_dir():
        return {"error": f"재개 거부: 이전 run을 찾을 수 없습니다({resume_run_id})"}
    lock_path = resume_dir / "resume.lock"
    try:
        # 재개 런 전체 동안 lineage를 독점한다. 24시간은 일반 backend timeout보다 충분히 길다.
        with reserve.file_lock(lock_path, ttl=86400, run_id=f"resume-{resume_run_id}"):
            return _run_task_file(
                cfg, task_file, auto=auto, ask=ask, resume_dir=resume_dir)
    except FileExistsError:
        return {"error": f"재개 거부: lineage 잠금 사용 중({resume_run_id})"}


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
