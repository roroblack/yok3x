"""배치 호출 예약 원장과 프로세스간 lockfile.

호출 수는 실행할 항목 수라 정확히 예약할 수 있다. 토큰과 비용은 프롬프트 길이로
계산한 추정 상한일 뿐이며, 실제 사용량은 실행 뒤 ``usage.record``가 기록한다.
"""
from __future__ import annotations

import json
import os
import time

from . import usage
from .config import Config


LOCK_NAME = "reservations.lock"
LEDGER_NAME = "reservations.json"


def _write_all(fd: int, data: bytes) -> None:
    """os.write의 부분 쓰기 가능성까지 처리한다."""
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("예약 파일 쓰기가 중단되었습니다")
        offset += written


def _read_json(path: os.PathLike | str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"예약 파일 형식 오류: {path}")
    return data


def _atomic_write_json(path: os.PathLike | str, data: dict) -> None:
    """같은 디렉터리의 임시 파일을 완성한 뒤 원자적으로 교체한다."""
    path = os.fspath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(
        parent, f".{os.path.basename(path)}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        _write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _lock_ts(path: str) -> float:
    try:
        data = _read_json(path)
        return float(data.get("ts", 0.0))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0


class _FileLock:
    def __init__(self, path: os.PathLike | str, ttl: float, run_id: str,
                 wait: float, poll: float):
        self.path = os.fspath(path)
        self.ttl = float(ttl)
        self.run_id = run_id
        self.wait = max(0.0, float(wait))
        self.poll = max(0.001, float(poll))
        self.token = f"{os.getpid()}-{time.time_ns()}"
        self.owner = {
            "pid": os.getpid(), "run_id": run_id,
            "ts": time.time(), "token": self.token,
        }

    def _try_create(self) -> bool:
        # stale 회수 중인 프로세스가 있으면 원장을 먼저 잡지 않는다.
        if os.path.exists(self.path + ".reclaim"):
            return False
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            _write_all(fd, json.dumps(self.owner, ensure_ascii=False).encode("utf-8"))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            raise
        os.close(fd)
        return True

    def _reclaim_stale(self, now: float) -> bool:
        if not os.path.exists(self.path) or now - _lock_ts(self.path) <= self.ttl:
            return False
        reclaim = self.path + ".reclaim"
        try:
            fd = os.open(reclaim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            _write_all(fd, self.token.encode("ascii"))
            os.close(fd)
            fd = None
            # 회수 권한을 잡은 뒤 다시 읽는다. 다른 프로세스의 새 락을 지우지 않는다.
            if os.path.exists(self.path) and now - _lock_ts(self.path) > self.ttl:
                try:
                    owner = _read_json(self.path)
                except (OSError, ValueError, json.JSONDecodeError):
                    owner = {}
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    return False
                print(
                    f"[reserve] stale lock 회수: pid={owner.get('pid', '?')} "
                    f"run_id={owner.get('run_id', '?')}", flush=True)
                return True
            return False
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(reclaim)
            except FileNotFoundError:
                pass

    def __enter__(self):
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)
        deadline = time.monotonic() + self.wait
        while True:
            if self._try_create():
                return self
            now = time.time()
            if self._reclaim_stale(now):
                continue
            if time.monotonic() >= deadline:
                raise FileExistsError(f"예약 lock 사용 중: {self.path}")
            time.sleep(min(self.poll, max(0.0, deadline - time.monotonic())))

    def __exit__(self, exc_type, exc, tb):
        # TTL 회수 뒤 다른 보유자가 생긴 경우 그 락을 지우지 않는다.
        try:
            owner = _read_json(self.path)
        except (OSError, ValueError, json.JSONDecodeError):
            owner = {}
        if owner.get("token") == self.token:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
        return False


def file_lock(path: os.PathLike | str, ttl: float = 300, *, run_id: str = "",
              wait: float = 0.0, poll: float = 0.05) -> _FileLock:
    """O_EXCL lockfile 컨텍스트 매니저. 살아 있는 중복 락은 획득하지 못한다."""
    return _FileLock(path, ttl, run_id, wait, poll)


def _reservation_cfg(cfg: Config) -> dict:
    return (cfg.yok3x.get("guard") or {}).get("reservation") or {}


def _paths(cfg: Config) -> tuple[str, str]:
    base = os.fspath(cfg.paths.yok3x_dir)
    return os.path.join(base, LOCK_NAME), os.path.join(base, LEDGER_NAME)


def _hard_limits(cfg: Config) -> tuple[dict[str, float], dict[str, float]]:
    """배치 전체에 적용할 보수적 hard 상한과 현재 사용량을 반환한다.

    API 원장이 backend별 세부량을 갖지 않으므로, 별도 override가 없으면 해당 지표를
    쓰는 backend 예산 중 가장 작은 값을 배치 전체 상한으로 삼는다. 이는 정확한 척
    분배하지 않고 안전한 쪽으로 예약하는 선택이다.
    """
    rcfg = _reservation_cfg(cfg)
    overrides = rcfg.get("hard_limits") or {}
    budgets = cfg.yok3x.get("budgets") or {}
    totals = usage.today_totals(cfg)
    metric_map = {
        "calls": "daily_calls", "est_tokens": "daily_tokens", "est_usd": "daily_usd",
    }
    limits_out: dict[str, float] = {}
    used_out: dict[str, float] = {}
    hard_ratio = float((cfg.yok3x.get("guard") or {}).get("hard_ratio", 1.0))
    for field, budget_key in metric_map.items():
        configured = float(overrides.get(field, 0) or 0)
        eligible = {
            backend: float(values.get(budget_key, 0) or 0)
            for backend, values in budgets.items()
            if isinstance(values, dict) and float(values.get(budget_key, 0) or 0) > 0
        }
        raw_limit = configured or (min(eligible.values()) if eligible else 0.0)
        limits_out[field] = raw_limit * hard_ratio
        total_key = {"calls": "calls", "est_tokens": "tokens", "est_usd": "usd"}[field]
        used_out[field] = sum(
            float(totals.get(backend, {}).get(total_key, 0) or 0)
            for backend in eligible)
    return limits_out, used_out


def _lock_options(cfg: Config) -> dict:
    rcfg = _reservation_cfg(cfg)
    return {
        "ttl": float(rcfg.get("lock_ttl_sec", 300)),
        "wait": float(rcfg.get("lock_wait_sec", 5.0)),
        "poll": float(rcfg.get("lock_poll_sec", 0.05)),
    }


def reserve(cfg: Config, run_id: str, calls: int, est_tokens: int,
            est_usd: float) -> bool:
    """오늘 실사용+pending+이번 배치가 hard 상한 이하면 원장에 예약한다.

    ``calls``만 정확한 예약이다. ``est_tokens``와 ``est_usd``는 보수적인 추정 예산이며,
    실행 후 실제치는 기존 ``usage.record``가 별도로 기록한다.
    """
    if not run_id or isinstance(calls, bool) or isinstance(est_tokens, bool):
        raise ValueError("run_id와 정수 예약량이 필요합니다")
    if not isinstance(calls, int) or not isinstance(est_tokens, int):
        raise ValueError("calls와 est_tokens는 정수여야 합니다")
    est_usd = float(est_usd)
    if calls < 0 or est_tokens < 0 or not (0.0 <= est_usd < float("inf")):
        raise ValueError("예약량은 유한한 0 이상 값이어야 합니다")

    lock_path, ledger_path = _paths(cfg)
    try:
        with file_lock(lock_path, run_id=run_id, **_lock_options(cfg)):
            ledger = _read_json(ledger_path)
            limits, used = _hard_limits(cfg)
            pending = {"calls": 0.0, "est_tokens": 0.0, "est_usd": 0.0}
            for other_id, row in ledger.items():
                if other_id == run_id or not isinstance(row, dict):
                    continue
                for field in pending:
                    pending[field] += float(row.get(field, 0) or 0)
            requested = {
                "calls": float(calls), "est_tokens": float(est_tokens), "est_usd": est_usd,
            }
            # 호출 수는 정확 hard 제약, 토큰·비용은 추정 상한을 보수적으로 같은 방식으로 거절한다.
            for field in ("calls", "est_tokens", "est_usd"):
                if limits[field] > 0 and used[field] + pending[field] + requested[field] > limits[field]:
                    return False
            ledger[run_id] = {
                "calls": calls, "est_tokens": est_tokens, "est_usd": est_usd,
                "ts": time.time(), "pid": os.getpid(),
            }
            _atomic_write_json(ledger_path, ledger)
            return True
    except FileExistsError:
        return False


def headroom(cfg: Config, exclude_run_id: str = "") -> dict[str, dict[str, float]] | None:
    """지표별 잔여 예산(remaining = limit − 실사용 − 다른 런의 pending)을 **원장 lock 아래에서**
    일관된 스냅샷으로 읽는다(R-3 preflight용).

    `reserve()`와 **같은 회계**(_hard_limits + pending 합산)를 재사용하는 게 핵심이다 — preflight가
    별도 계산을 두면 예약 경로와 어긋나 서로 다른 판정을 내린다(codex 지적: '단순 검사'가 아니라
    예약 원장 재사용). 예약을 쓰지는 않으므로 이중 계상이 없고, 실제 강제는 기존 배치 `reserve()`가
    원자적으로 수행한다. limit이 0인 지표는 상한 없음(enforcement off)이라 remaining=inf.

    lock 획득 실패 시 None(알 수 없음) — preflight는 이때 판단을 보류한다(런을 막지 않음).
    """
    lock_path, ledger_path = _paths(cfg)
    try:
        with file_lock(lock_path, run_id=f"headroom-{os.getpid()}", **_lock_options(cfg)):
            ledger = _read_json(ledger_path)
            limits, used = _hard_limits(cfg)
            out: dict[str, dict[str, float]] = {}
            for field in ("calls", "est_tokens", "est_usd"):
                pending = 0.0
                for other_id, row in ledger.items():
                    if other_id == exclude_run_id or not isinstance(row, dict):
                        continue
                    pending += float(row.get(field, 0) or 0)
                limit = float(limits.get(field, 0) or 0)
                used_v = float(used.get(field, 0) or 0)
                out[field] = {
                    "limit": limit, "used": used_v, "pending": pending,
                    "remaining": (limit - used_v - pending) if limit > 0 else float("inf"),
                }
            return out
    except FileExistsError:
        return None


def release(cfg: Config, run_id: str) -> None:
    """배치 실행 종료 후 pending 예약만 해제한다. 실사용 기록은 지우지 않는다."""
    lock_path, ledger_path = _paths(cfg)
    try:
        with file_lock(lock_path, run_id=run_id, **_lock_options(cfg)):
            ledger = _read_json(ledger_path)
            if run_id in ledger:
                del ledger[run_id]
                _atomic_write_json(ledger_path, ledger)
    except FileExistsError:
        # 다른 프로세스가 오래 원장을 쓰는 중이면 해제를 조용히 유실하지 않는다.
        raise RuntimeError("예약 원장 lock을 획득하지 못해 해제할 수 없습니다")


def cleanup_stale(cfg: Config, ttl: float = 1800) -> int:
    """TTL이 지난 pending 예약만 회수하고 회수 수를 반환한다."""
    lock_path, ledger_path = _paths(cfg)
    try:
        with file_lock(lock_path, run_id=f"cleanup-{os.getpid()}", **_lock_options(cfg)):
            ledger = _read_json(ledger_path)
            now = time.time()
            stale = []
            for run_id, row in ledger.items():
                if not isinstance(row, dict):
                    continue
                try:
                    ts = float(row.get("ts", now))
                except (TypeError, ValueError):
                    continue
                if now - ts > ttl:
                    stale.append(run_id)
            for run_id in stale:
                del ledger[run_id]
            if stale:
                _atomic_write_json(ledger_path, ledger)
                print(f"[reserve] stale 예약 {len(stale)}개 회수", flush=True)
            return len(stale)
    except FileExistsError:
        return 0
