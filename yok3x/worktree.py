"""R-7: 병렬 워커용 git worktree 격리 (의존성0 — git CLI만 사용).

**왜**: 병렬 fanout은 지금까지 모든 워커가 **같은 workdir**에서 실행됐다. 워커는 텍스트 생산자라
파일 게시는 오케스트레이터가 대신 하지만(`artifacts`), 에이전트 CLI는 실행 cwd를 읽고 때때로 임시
파일을 쓴다 — 동시에 같은 트리를 밟으면 서로의 중간 상태를 보거나 덮어쓴다. 워커마다 같은 커밋의
독립 체크아웃을 주면 간섭이 사라지고 **사용자의 실제 작업 트리도 보호**된다.

**중요한 트레이드오프(그래서 기본 off)**: worktree는 **HEAD 커밋**을 체크아웃한다. 즉 사용자 작업
트리의 **커밋되지 않은 변경은 보이지 않는다.** 이걸 기본으로 켜면 "에이전트가 방금 내 수정을 못 본다"는
조용한 동작 변화가 생긴다. 그래서 `guard.parallel.worktree_isolation`은 **opt-in**이고, 켤 수 없는
상황(비-git·git 없음·커밋 없음·실패)에서는 **명시 로그를 남기고 기존 동작(공유 workdir)으로 폴백**한다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_TIMEOUT = 60


def _git(args: list[str], cwd: str | Path | None = None,
         timeout: int = _TIMEOUT) -> tuple[bool, str]:
    """git 실행. (성공여부, 출력). git이 없거나 실패해도 예외를 올리지 않는다(호출자가 폴백)."""
    exe = shutil.which("git")
    if not exe:
        return False, "git 실행파일 없음"
    try:
        proc = subprocess.run([exe, *args], cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"git timeout({timeout}s): {' '.join(args)}"
    except OSError as exc:
        return False, f"git 실행 실패: {type(exc).__name__}: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, out


def repo_root(path: str | Path) -> str | None:
    """path가 속한 git 저장소 최상위. 저장소가 아니거나 git이 없으면 None."""
    ok, out = _git(["rev-parse", "--show-toplevel"], cwd=path)
    return out.strip() if ok and out.strip() else None


def has_commit(repo: str | Path) -> bool:
    """HEAD가 가리키는 커밋이 있는가(빈 저장소면 worktree add가 불가)."""
    ok, _ = _git(["rev-parse", "--verify", "HEAD"], cwd=repo)
    return ok


def add(repo: str | Path, dest: str | Path, ref: str = "HEAD") -> tuple[bool, str]:
    """`git worktree add --detach <dest> <ref>`. 성공 시 (True, 경로)."""
    dest = Path(dest)
    if dest.exists():
        return False, f"대상 경로가 이미 있음: {dest}"
    ok, out = _git(["worktree", "add", "--detach", str(dest), ref], cwd=repo)
    if not ok:
        return False, out
    return True, str(dest)


def remove(repo: str | Path, dest: str | Path) -> tuple[bool, str]:
    """worktree 제거 후 prune. 실패해도 호출자가 런을 깨지 않게 (False, 사유)만 돌린다."""
    ok, out = _git(["worktree", "remove", "--force", str(dest)], cwd=repo)
    if not ok:
        # 이미 지워졌거나 등록이 깨진 경우: 디렉터리를 직접 정리하고 prune으로 등록만 회수한다.
        try:
            if Path(dest).exists():
                shutil.rmtree(dest, ignore_errors=True)
        except OSError:
            pass
    _git(["worktree", "prune"], cwd=repo)
    return ok, out


def usable(workdir: str | Path | None) -> tuple[str | None, str]:
    """격리를 쓸 수 있는지 판단한다. (repo_root 또는 None, 사유).

    쓸 수 없으면 사유 문자열로 **왜 폴백하는지** 알린다 — 조용한 열화를 만들지 않는다(RULE §5.5).
    """
    if not workdir:
        return None, "workdir 없음(격리 불필요 — 이미 빈 임시 dir에서 실행)"
    if not shutil.which("git"):
        return None, "git 실행파일 없음"
    root = repo_root(workdir)
    if not root:
        return None, f"git 저장소 아님: {workdir}"
    if not has_commit(root):
        return None, "커밋 없는 저장소(HEAD 없음) — worktree 생성 불가"
    return root, "ok"
