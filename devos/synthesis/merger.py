"""Code merger — copies worktree files into the repo root and cleans up.

merge() preconditions:
  - report.passed must be True (report.errors == 0).
  - If errors > 0, raises MergeBlockedError with the failing findings.

Merge sequence:
  1. Read task_graph.json to get the dependency-order task list (wave 0 first).
  2. For each TaskOutput in that order, copy every file from
     worktree_path / rel_file → repo_root / rel_file, creating parent dirs.
  3. After all files copied, remove worktrees (best-effort; log warning on fail).
  4. Write .devos/synthesis_manifest.json.
  5. Return MergeResult.

Last-writer-wins is safe because file overlap is an *error* in the validator —
if the merger runs, the validator has already confirmed no two tasks write the
same file.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from devos.synthesis.collector import CollectedOutputs
from devos.synthesis.validator import Finding, ValidationReport
from devos.execution.worktree import WorktreeManager

logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────────


class MergeBlockedError(Exception):
    """Raised when the validation report has errors that block the merge.

    Attributes:
        findings: The error-level findings that blocked the merge.
    """

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class MergerError(Exception):
    """Raised for I/O failures during the merge that are not validation errors."""


# ── Result dataclass ────────────────────────────────────────────────────────────


@dataclass
class MergeResult:
    """Outcome of a successful merge.

    Attributes:
        files_written:  All relative paths written to the repository root.
        tasks_merged:   Task IDs whose files were merged (in wave order).
        warnings:       Non-blocking warning messages from the ValidationReport.
        manifest_path:  Absolute path to the written synthesis_manifest.json.
    """

    files_written: list[Path] = field(default_factory=list)
    tasks_merged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest_path: Path = Path()


# ── Main class ──────────────────────────────────────────────────────────────────


class CodeMerger:
    """Copies worktree outputs into the repository root and removes worktrees.

    Args:
        repo_root:  Root of the git repository (merge destination).
        devos_dir:  Path to .devos/ runtime directory.
    """

    def __init__(self, repo_root: Path, devos_dir: Path) -> None:
        self._repo_root = repo_root
        self._devos_dir = devos_dir
        self._worktree_manager = WorktreeManager(repo_root)

    def merge(
        self,
        outputs: CollectedOutputs,
        report: ValidationReport,
    ) -> MergeResult:
        """Copy all worktree files into the repo root in wave/dependency order.

        Args:
            outputs: CollectedOutputs from OutputCollector.collect().
            report:  ValidationReport from SynthesisValidator.validate().

        Returns:
            MergeResult with lists of files written and tasks merged.

        Raises:
            MergeBlockedError: If report.errors > 0.
            MergerError:       If a critical I/O failure occurs during copying.
        """
        if report.errors > 0:
            error_findings = [f for f in report.findings if f.level == "error"]
            raise MergeBlockedError(
                f"Merge blocked — {report.errors} error(s) must be resolved before merging.",
                findings=error_findings,
            )

        # Determine task order from task_graph.json (wave 0 first).
        ordered_task_ids = self._ordered_task_ids()

        files_written: list[Path] = []
        tasks_merged: list[str] = []

        for task_id in ordered_task_ids:
            task_output = outputs.task_outputs.get(task_id)
            if task_output is None:
                continue  # task has no output — skip (completeness check already warned)

            for rel_file in task_output.files:
                source = task_output.worktree_path / rel_file
                dest = self._repo_root / rel_file

                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    files_written.append(rel_file)
                except OSError as exc:
                    raise MergerError(
                        f"Failed to copy {source} → {dest}: {exc}"
                    ) from exc

            tasks_merged.append(task_id)

        # Remove worktrees (best-effort — they may have been removed already).
        for task_id in ordered_task_ids:
            if task_id not in outputs.task_outputs:
                continue
            try:
                self._worktree_manager.remove(task_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not remove worktree for %s (may already be cleaned up): %s",
                    task_id,
                    exc,
                )

        # Write synthesis manifest.
        warnings = [f.message for f in report.findings if f.level == "warning"]
        manifest_path = self._write_manifest(tasks_merged, files_written, warnings)

        return MergeResult(
            files_written=files_written,
            tasks_merged=tasks_merged,
            warnings=warnings,
            manifest_path=manifest_path,
        )

    # ── Private ────────────────────────────────────────────────────────────────

    def _ordered_task_ids(self) -> list[str]:
        """Return task IDs in wave-ascending order from task_graph.json.

        Falls back to an empty list if the file is missing or unreadable.
        """
        task_graph_path = self._devos_dir / "task_graph.json"
        if not task_graph_path.exists():
            logger.warning("task_graph.json missing — merge order will be arbitrary")
            return []

        try:
            task_graph = json.loads(task_graph_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read task_graph.json: %s", exc)
            return []

        ids: list[str] = []
        for wave in sorted(task_graph.get("waves", []), key=lambda w: w["wave"]):
            for task in wave.get("tasks", []):
                ids.append(task["id"])
        return ids

    def _write_manifest(
        self,
        tasks_merged: list[str],
        files_written: list[Path],
        warnings: list[str],
    ) -> Path:
        """Write .devos/synthesis_manifest.json and return its path."""
        manifest = {
            "merged_at": datetime.now(timezone.utc).isoformat(),
            "tasks_merged": tasks_merged,
            "files_written": [str(f) for f in files_written],
            "warnings": warnings,
        }
        manifest_path = self._devos_dir / "synthesis_manifest.json"
        try:
            manifest_path.write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise MergerError(f"Failed to write synthesis manifest: {exc}") from exc
        return manifest_path
