from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(project_path: str | Path, *args: str) -> str:
    cmd = ["git", "-C", str(project_path), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "git command failed").strip()
        raise RuntimeError(msg)
    return result.stdout


def create_task_worktree(project_path: str | Path, task_slug: str, base_branch: str) -> dict[str, str]:
    slug = (task_slug or "").strip()
    if not slug:
        raise ValueError("task_slug is required")
    branch = f"task/{slug}"
    root = Path(project_path).resolve()
    worktree_path = root.parent / f"{root.name}-{slug}"
    _run_git(root, "worktree", "add", "-b", branch, str(worktree_path), base_branch)
    return {"branch": branch, "worktree_path": str(worktree_path)}


def list_worktrees(project_path: str | Path) -> list[dict[str, str | bool]]:
    out = _run_git(project_path, "worktree", "list", "--porcelain")
    entries: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] | None = None
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            if current:
                entries.append(current)
                current = None
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line.split(" ", 1)[1], "detached": False}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            branch_ref = line.split(" ", 1)[1]
            current["branch"] = branch_ref.removeprefix("refs/heads/")
        elif line == "detached":
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def remove_worktree(project_path: str | Path, worktree_path: str | Path, force: bool = False) -> None:
    wt_path = Path(worktree_path)
    if not force:
        status = _run_git(wt_path, "status", "--porcelain")
        if status.strip():
            raise RuntimeError("worktree has uncommitted changes; use force=True to remove")
    args = ["worktree", "remove", str(wt_path)]
    if force:
        args.append("--force")
    _run_git(project_path, *args)


def merge_task_branch(project_path: str | Path, task_branch: str, target_branch: str) -> None:
    _run_git(project_path, "rev-parse", "--verify", task_branch)
    _run_git(project_path, "checkout", target_branch)
    _run_git(project_path, "diff", "--quiet")
    _run_git(project_path, "merge", "--no-ff", task_branch)
