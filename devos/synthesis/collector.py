"""Output collector — gathers all worktree outputs after wave execution.

For each task in the task graph the collector:
  1. Reads .devos/sessions/{task_id}/state.json and skips tasks whose
     status != "complete" (records them in missing_tasks with a warning).
  2. Reads .devos/summaries/{task_id}.md and parses it into a DistilledSummary
     (skips and records in missing_tasks if the file is absent).
  3. Lists every file inside .devos/worktrees/{task_id}/ that is NOT under
     .devos/ or .git/ — relative to the worktree root.

collect() NEVER raises on missing or incomplete data.  It collects what
exists and records every gap in CollectedOutputs.missing_tasks.  The
SynthesisValidator decides what to do with those gaps.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from devos.context.distiller import DistilledSummary, load_summary

logger = logging.getLogger(__name__)

# Directories inside a worktree that are never part of the produced output.
_EXCLUDED_DIRS: frozenset[str] = frozenset({".devos", ".git"})


# ── Data structures ─────────────────────────────────────────────────────────────


@dataclass
class TaskOutput:
    """Collected output for a single completed task.

    Attributes:
        task_id:       Task identifier (e.g. ``T-001``).
        summary:       Parsed DistilledSummary from the .md file.
        files:         Relative paths of files found in the worktree
                       (empty if the worktree was already removed).
        worktree_path: Absolute path to the worktree root.
    """

    task_id: str
    summary: DistilledSummary
    files: list[Path]
    worktree_path: Path


@dataclass
class CollectedOutputs:
    """Aggregated collection of all task outputs.

    Attributes:
        task_outputs:  task_id → TaskOutput for every task that was
                       collected successfully (status=complete AND
                       summary file present).
        missing_tasks: task_ids that could not be collected (incomplete
                       status, absent summary, or absent state.json).
        total_files:   Sum of len(output.files) across all task_outputs.
    """

    task_outputs: dict[str, TaskOutput] = field(default_factory=dict)
    missing_tasks: list[str] = field(default_factory=list)
    total_files: int = 0


# ── Main class ──────────────────────────────────────────────────────────────────


class OutputCollector:
    """Gathers worktree outputs for all tasks after wave execution completes.

    Args:
        repo_root:  Root of the git repository.
        devos_dir:  Path to .devos/ runtime directory.
    """

    def __init__(self, repo_root: Path, devos_dir: Path) -> None:
        self._repo_root = repo_root
        self._devos_dir = devos_dir

    def collect(self, task_graph: dict) -> CollectedOutputs:
        """Collect outputs for every task listed in the task graph.

        Iterates all tasks across all waves.  For each task:
          - Checks state.json exists and status == "complete".
          - Parses the distilled summary .md file.
          - Lists files in the worktree (empty list if worktree is gone).

        Any task that cannot be fully collected is added to ``missing_tasks``
        with a logged warning.  This method never raises.

        Args:
            task_graph: Parsed task_graph.json dict (must contain "waves").

        Returns:
            CollectedOutputs with every collectible task populated.
        """
        result = CollectedOutputs()

        for wave in sorted(task_graph.get("waves", []), key=lambda w: w["wave"]):
            for task_dict in wave.get("tasks", []):
                task_id: str = task_dict["id"]
                collected = self._collect_one(task_id)
                if collected is None:
                    result.missing_tasks.append(task_id)
                else:
                    result.task_outputs[task_id] = collected
                    result.total_files += len(collected.files)

        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _collect_one(self, task_id: str) -> TaskOutput | None:
        """Attempt to collect output for a single task.

        Returns None (and logs a warning) if the task is not complete or
        any required file is missing.
        """
        # ── Step 1: Read state.json ────────────────────────────────────────
        state_path = self._devos_dir / "sessions" / task_id / "state.json"
        if not state_path.exists():
            logger.warning("state.json missing for %s — skipping", task_id)
            return None

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state.json for %s: %s", task_id, exc)
            return None

        if state.get("status") != "complete":
            logger.warning(
                "Task %s status is %r (not 'complete') — skipping",
                task_id,
                state.get("status"),
            )
            return None

        # Recover worktree path from state.json (may not exist on disk any more).
        worktree_path = Path(state.get("worktree_path", ""))

        # ── Step 2: Read summary file ─────────────────────────────────────
        summary_path = self._devos_dir / "summaries" / f"{task_id}.md"
        if not summary_path.exists():
            logger.warning("Summary file missing for %s — skipping", task_id)
            return None

        try:
            summary = load_summary(summary_path)
        except OSError as exc:
            logger.warning("Could not read summary for %s: %s", task_id, exc)
            return None

        # ── Step 3: List files in worktree ────────────────────────────────
        files = _list_worktree_files(worktree_path)

        return TaskOutput(
            task_id=task_id,
            summary=summary,
            files=files,
            worktree_path=worktree_path,
        )


# ── Private helpers ─────────────────────────────────────────────────────────────


def _list_worktree_files(worktree_path: Path) -> list[Path]:
    """Return relative paths of files *modified* in the worktree vs HEAD.

    Uses ``git diff --name-only HEAD`` so only files genuinely written or
    changed by the task are returned — not inherited base-repo files that
    exist in every worktree unchanged.

    If the worktree no longer exists, git is unavailable, or the command
    fails, an empty list is returned rather than raising.

    Args:
        worktree_path: Absolute path to the worktree root.

    Returns:
        Sorted list of relative ``Path`` objects for modified files only.
    """
    if not worktree_path.exists():
        return []

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "git diff failed in %s (rc=%d): %s",
                worktree_path,
                result.returncode,
                result.stderr.strip(),
            )
            return []
    except OSError as exc:
        logger.warning("Could not run git diff in %s: %s", worktree_path, exc)
        return []

    files: list[Path] = []
    for name in result.stdout.splitlines():
        name = name.strip()
        if not name:
            continue
        rel = Path(name)
        # Exclude .devos/ and .git/ subtrees (defensive; git normally won't
        # track these, but guard against unusual repo configurations).
        if rel.parts and rel.parts[0] in _EXCLUDED_DIRS:
            continue
        files.append(rel)

    return sorted(files)
