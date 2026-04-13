"""Git worktree management for isolated agent execution.

Each task receives its own git worktree at .devos/worktrees/{task_id}/.
Agents NEVER share a working directory — worktree isolation is non-negotiable.

All git commands:
  - Use subprocess.run with check=True, capture_output=True, text=True
  - Use cwd=repo_root
  - Raise WorktreeError on failure (wrapping CalledProcessError)
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Raised when a git worktree operation fails."""


class WorktreeManager:
    """Creates and removes isolated git worktrees for agent tasks.

    Args:
        repo_root: Root of the git repository containing .devos/.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def create(self, task_id: str) -> Path:
        """Create an isolated git worktree for task_id at HEAD.

        Command: git worktree add .devos/worktrees/{task_id} HEAD

        Args:
            task_id: Task identifier (e.g. 'T-001').

        Returns:
            Path to the newly created worktree directory.

        Raises:
            WorktreeError: If the git command fails (e.g. worktree already exists,
                           not a git repo, or detached HEAD).
        """
        worktree_path = self._repo_root / ".devos" / "worktrees" / task_id
        try:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), "HEAD"],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeError(
                f"Failed to create worktree for {task_id}: {exc.stderr.strip()}"
            ) from exc
        return worktree_path

    def remove(self, task_id: str) -> None:
        """Remove the git worktree for task_id (force-removes unclean trees).

        Command: git worktree remove --force .devos/worktrees/{task_id}

        Args:
            task_id: Task identifier whose worktree should be removed.

        Raises:
            WorktreeError: If the worktree is not found or removal fails.
        """
        worktree_path = self._repo_root / ".devos" / "worktrees" / task_id
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=self._repo_root,
                capture_output=True,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise WorktreeError(
                f"Failed to remove worktree for {task_id}: {exc.stderr.strip()}"
            ) from exc

    def list_active(self) -> list[str]:
        """Return task_ids of all active DevOS worktrees.

        Parses ``git worktree list --porcelain`` output and identifies
        worktrees located under .devos/worktrees/.

        Returns:
            List of task_id strings (e.g. ['T-001', 'T-003']).
        """
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self._repo_root,
            capture_output=True,
            check=True,
            text=True,
        )
        task_ids: list[str] = []
        worktrees_base = self._repo_root / ".devos" / "worktrees"

        for line in result.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            wt_path = Path(line[len("worktree "):].strip())
            try:
                rel = wt_path.relative_to(worktrees_base)
            except ValueError:
                continue  # Not a DevOS worktree
            parts = rel.parts
            if parts:
                task_ids.append(parts[0])

        return task_ids
