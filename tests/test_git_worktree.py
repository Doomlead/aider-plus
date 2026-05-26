from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aider.git_worktree import (
    create_task_worktree,
    list_worktrees,
    merge_task_branch,
    remove_worktree,
)


def run(cmd: list[str], cwd: Path):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(["git", "init", "-b", "main"], repo)
    run(["git", "config", "user.email", "test@example.com"], repo)
    run(["git", "config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    run(["git", "add", "README.md"], repo)
    run(["git", "commit", "-m", "init"], repo)
    return repo


def test_create_and_list_worktree(tmp_path: Path):
    repo = init_repo(tmp_path)
    info = create_task_worktree(repo, "feature-a", "main")

    assert info["branch"] == "task/feature-a"
    assert Path(info["worktree_path"]).exists()

    worktrees = list_worktrees(repo)
    branches = {entry.get("branch") for entry in worktrees}
    assert "main" in branches
    assert "task/feature-a" in branches


def test_remove_worktree_refuses_dirty_without_force(tmp_path: Path):
    repo = init_repo(tmp_path)
    info = create_task_worktree(repo, "dirty", "main")
    wt = Path(info["worktree_path"])
    (wt / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        remove_worktree(repo, wt)

    remove_worktree(repo, wt, force=True)
    assert not wt.exists()


def test_merge_task_branch(tmp_path: Path):
    repo = init_repo(tmp_path)
    info = create_task_worktree(repo, "merge-me", "main")
    wt = Path(info["worktree_path"])
    (wt / "feature.txt").write_text("hello\n", encoding="utf-8")
    run(["git", "add", "feature.txt"], wt)
    run(["git", "commit", "-m", "feat"], wt)
    merge_task_branch(repo, "task/merge-me", "main")

    assert (repo / "feature.txt").exists()
