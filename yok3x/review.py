"""읽기전용 review bundle을 표시하고 사람이 승인한 후보만 안전하게 승격한다."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from . import artifacts

DIFF_DISPLAY_MAX_BYTES = 100_000
MANIFEST_MAX_BYTES = 10_000_000
CANDIDATE_MAX_BYTES = 512_000
_VALID_STATUS = {"new", "modified", "unchanged"}


class ReviewError(ValueError):
    """검토 번들을 찾거나 신뢰할 수 없을 때의 사용자 입력 오류."""


@dataclass(frozen=True)
class ApplyDecision:
    allowed: bool
    reason: str


def decide_file_application(
        record: Mapping[str, Any], *, current_exists: bool,
        current_sha256: str | None, candidate_sha256: str | None,
        path_within_workdir: bool = True, path_has_symlink: bool = False,
        candidate_within_bundle: bool = True, candidate_has_symlink: bool = False,
        current_is_file: bool = True) -> ApplyDecision:
    """관측값만으로 한 후보의 적용 가능 여부를 판정한다(파일시스템 I/O 없음)."""
    path = record.get("path")
    status = record.get("status")
    if not isinstance(path, str):
        return ApplyDecision(False, "changes.json 경로 형식 오류")
    path_reason = artifacts.validate_relative_path(path)
    if path_reason:
        return ApplyDecision(False, f"위험한 경로: {path_reason}")
    if not path_within_workdir:
        return ApplyDecision(False, "대상 경로가 workdir 밖")
    if path_has_symlink:
        return ApplyDecision(False, "대상 경로에 심볼릭 링크가 있음")
    if not candidate_within_bundle:
        return ApplyDecision(False, "후보 경로가 번들 밖")
    if candidate_has_symlink:
        return ApplyDecision(False, "후보 경로에 심볼릭 링크가 있음")
    if status not in _VALID_STATUS:
        return ApplyDecision(False, f"알 수 없는 status: {status!r}")

    proposed = record.get("proposed_sha256")
    if not _valid_sha256(proposed):
        return ApplyDecision(False, "proposed_sha256 형식 오류")
    if candidate_sha256 != proposed:
        return ApplyDecision(False, "후보 내용 해시 불일치(번들 손상 가능)")

    base = record.get("base_sha256")
    if status == "new":
        if base is not None:
            return ApplyDecision(False, "new 파일의 base_sha256가 null이 아님")
        if current_exists:
            return ApplyDecision(False, "신규 대상이 이미 존재함(덮어쓰기 방지)")
        return ApplyDecision(True, "base 없음 확인")

    if not _valid_sha256(base):
        return ApplyDecision(False, "base_sha256 형식 오류")
    if not current_exists:
        return ApplyDecision(False, "기존 대상이 없어짐(base 불일치)")
    if not current_is_file:
        return ApplyDecision(False, "대상이 일반 파일이 아님")
    if current_sha256 != base:
        return ApplyDecision(False, "현재 파일의 base 해시 불일치(스테일 후보)")
    return ApplyDecision(True, "base 해시 일치")


def _valid_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_uses_symlink(base: Path, relative_path: str) -> bool:
    current = base
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _atomic_write_bytes(
        path: Path, data: bytes, before_replace: Callable[[], None] | None = None) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                suffix=".yok3x.tmp", delete=False) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        if before_replace is not None:
            before_replace()
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _safe_run_id(run_id: str) -> bool:
    return (bool(run_id) and run_id not in {".", ".."}
            and Path(run_id).name == run_id and "/" not in run_id and "\\" not in run_id)


def _is_bundle_root(path: Path, run_id: str) -> bool:
    return (path.name == run_id and path.parent.name == "yok3x-out"
            and (path / "changes.json").is_file()
            and (path / "changes.diff").is_file())


def find_bundle(cfg, run_id: str) -> Path:
    """status의 정확한 root를 우선하고 workdir/run_dir 관례 위치를 차례로 찾는다."""
    if not _safe_run_id(run_id):
        raise ReviewError(f"잘못된 run_id: {run_id!r}")

    run_dir = cfg.paths.runs / run_id
    status_path = run_dir / "status.json"
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8-sig"))
            changes = status.get("changes") if isinstance(status, dict) else None
            raw_root = changes.get("root") if isinstance(changes, dict) else None
            if isinstance(raw_root, str) and raw_root:
                pointed = Path(raw_root)
                if not pointed.is_absolute():
                    pointed = cfg.paths.root / pointed
                if _is_bundle_root(pointed, run_id):
                    return pointed
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    candidates = [cfg.paths.root / "yok3x-out" / run_id]
    workspace = cfg.yok3x.get("workspace")
    if workspace:
        workspace_path = Path(workspace)
        if not workspace_path.is_absolute():
            workspace_path = cfg.paths.root / workspace_path
        candidates.append(workspace_path / "yok3x-out" / run_id)
    candidates.append(run_dir / "yok3x-out" / run_id)
    for candidate in candidates:
        if _is_bundle_root(candidate, run_id):
            return candidate
    checked = ", ".join(str(p) for p in candidates)
    raise ReviewError(f"review bundle 없음: run_id={run_id} (확인: {checked})")


def load_bundle(root: Path) -> dict[str, Any]:
    manifest = root / "changes.json"
    diff = root / "changes.diff"
    if root.is_symlink() or root.parent.is_symlink():
        raise ReviewError("review bundle 경로가 심볼릭 링크임")
    if manifest.is_symlink() or diff.is_symlink():
        raise ReviewError("review bundle 메타파일이 심볼릭 링크임")
    try:
        if manifest.stat().st_size > MANIFEST_MAX_BYTES:
            raise ReviewError(f"changes.json 크기 상한({MANIFEST_MAX_BYTES}B) 초과")
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except ReviewError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"changes.json 읽기 실패: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict) or data.get("mode") != "review":
        raise ReviewError("changes.json mode가 review가 아님")
    files = data.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise ReviewError("changes.json files 형식 오류")
    seen: set[str] = set()
    for item in files:
        path = item.get("path")
        if not isinstance(path, str):
            raise ReviewError("changes.json 파일 경로 형식 오류")
        key = path.replace("\\", "/").lower()
        if key in seen:
            raise ReviewError(f"changes.json 중복/대소문자 충돌 경로: {path}")
        seen.add(key)
    return data


def show_bundle(root: Path, bundle: Mapping[str, Any]) -> None:
    print(f"검토 번들: {root}")
    for record in bundle["files"]:
        print(f"  [{record.get('status', '?')}] {record.get('path', '?')}  "
              f"base={record.get('base_bytes', '?')}B → proposed={record.get('proposed_bytes', '?')}B")
    for rejected in bundle.get("rejected", []) or []:
        if isinstance(rejected, dict):
            print(f"  [번들 스킵] {rejected.get('path', '?')} — {rejected.get('reason', '?')}")

    diff_path = root / "changes.diff"
    try:
        with diff_path.open("rb") as stream:
            raw = stream.read(DIFF_DISPLAY_MAX_BYTES + 1)
    except OSError as exc:
        raise ReviewError(f"changes.diff 읽기 실패: {type(exc).__name__}: {exc}") from exc
    truncated = len(raw) > DIFF_DISPLAY_MAX_BYTES
    shown = raw[:DIFF_DISPLAY_MAX_BYTES].decode("utf-8", errors="replace")
    print("\n--- changes.diff ---")
    print(shown, end="" if shown.endswith("\n") or not shown else "\n")
    if truncated:
        print(f"[diff 출력 상한 {DIFF_DISPLAY_MAX_BYTES}B에서 잘림]")


def accept_bundle(root: Path, bundle: Mapping[str, Any], paths: list[str]) -> int:
    """선택 후보를 all-settled 방식으로 적용하고 중단 파일 수를 반환한다."""
    workdir = root.parent.parent
    real_workdir = workdir.resolve()
    records = {item["path"]: item for item in bundle["files"]}
    selected = list(dict.fromkeys(paths)) if paths else list(records)
    applied = blocked = skipped = 0

    for path in selected:
        record = records.get(path)
        if record is None:
            blocked += 1
            print(f"[중단] {path} — 번들에 없는 경로")
            continue
        if record.get("status") == "unchanged":
            skipped += 1
            print(f"[스킵] {path} — status=unchanged")
            continue

        candidate = root / path
        target = workdir / path
        lexical_safe = not artifacts.validate_relative_path(path)
        candidate_within = _inside(candidate, root) if lexical_safe else False
        target_within = _inside(target, real_workdir) if lexical_safe else False
        candidate_symlink = _path_uses_symlink(root, path) if lexical_safe else False
        target_symlink = _path_uses_symlink(workdir, path) if lexical_safe else False

        candidate_raw: bytes | None = None
        candidate_hash: str | None = None
        observation_error = ""
        safe_candidate_read = lexical_safe and candidate_within and not candidate_symlink
        if safe_candidate_read:
            try:
                if not candidate.is_file():
                    observation_error = "후보 파일이 없거나 일반 파일이 아님"
                else:
                    # F2-11: stat 후 무제한 read_bytes()면 그 사이 파일이 커져도 그대로 읽는다
                    # (stat-then-read TOCTOU). 열린 스트림에서 상한+1만 읽어 판정한다 —
                    # orchestrator의 base 읽기(REVIEW_BASE_MAX_BYTES)와 같은 패턴으로 통일.
                    with candidate.open("rb") as fh:
                        raw = fh.read(CANDIDATE_MAX_BYTES + 1)
                    if len(raw) > CANDIDATE_MAX_BYTES:
                        observation_error = f"후보 크기 상한({CANDIDATE_MAX_BYTES}B) 초과"
                    else:
                        candidate_raw = raw
                        candidate_hash = _sha256(candidate_raw)
            except OSError as exc:
                observation_error = f"후보 읽기 실패: {type(exc).__name__}: {exc}"

        safe_target_read = lexical_safe and target_within and not target_symlink
        current_exists = (target.exists() or target.is_symlink()) if safe_target_read else False
        current_is_file = target.is_file() if current_exists else True
        current_hash: str | None = None
        if current_exists and current_is_file and not target_symlink:
            try:
                current_hash = _sha256_file(target)
            except OSError as exc:
                observation_error = observation_error or (
                    f"현재 파일 읽기 실패: {type(exc).__name__}: {exc}")

        decision = decide_file_application(
            record, current_exists=current_exists, current_sha256=current_hash,
            candidate_sha256=candidate_hash, path_within_workdir=target_within,
            path_has_symlink=target_symlink, candidate_within_bundle=candidate_within,
            candidate_has_symlink=candidate_symlink, current_is_file=current_is_file)
        if observation_error or not decision.allowed:
            blocked += 1
            print(f"[중단] {path} — {observation_error or decision.reason}")
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # mkdir와 교체 사이에도 경로를 다시 확인해 심볼릭 경합 창을 줄인다.
            if _path_uses_symlink(workdir, path) or not _inside(target, real_workdir):
                raise OSError("쓰기 직전 대상 경로가 심볼릭/외부 경로로 바뀜")

            def recheck_base() -> None:
                """완성된 임시 파일을 교체하기 직전에 base를 한 번 더 확인한다."""
                if _path_uses_symlink(workdir, path) or not _inside(target, real_workdir):
                    raise OSError("교체 직전 대상 경로가 심볼릭/외부 경로로 바뀜")
                exists_now = target.exists() or target.is_symlink()
                if record.get("status") == "new":
                    if exists_now:
                        raise OSError("교체 직전 신규 대상이 생김")
                    return
                if not exists_now or not target.is_file():
                    raise OSError("교체 직전 기존 대상이 없거나 일반 파일이 아님")
                if _sha256_file(target) != record.get("base_sha256"):
                    raise OSError("교체 직전 base 해시가 바뀜")

            _atomic_write_bytes(target, candidate_raw or b"", before_replace=recheck_base)
        except OSError as exc:
            blocked += 1
            print(f"[중단] {path} — 원자적 쓰기 실패: {type(exc).__name__}: {exc}")
            continue
        applied += 1
        print(f"[적용] {path} — {len(candidate_raw or b'')}B")

    print(f"요약: 적용 {applied} · 중단 {blocked} · 스킵 {skipped}")
    return blocked


def reject_bundle(root: Path, run_id: str) -> None:
    """workdir는 건드리지 않고 번들 안 감사 로그에 사람의 거절 결정을 남긴다."""
    log_path = root / "review.log"
    if log_path.is_symlink():
        raise ReviewError("review.log가 심볼릭 링크라 거절 기록을 쓰지 않음")
    previous = log_path.read_bytes() if log_path.exists() else b""
    line = f"{datetime.now().isoformat(timespec='seconds')} rejected run_id={run_id}\n".encode()
    _atomic_write_bytes(log_path, previous + line)
    print(f"거절됨: {run_id} (workdir 변경 없음, 번들 유지)")
