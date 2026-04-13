"""Synthesis validator — verifies collected outputs against the spec.

Five checks run in order (each appends to the findings list):

  1. Completeness   — every task_id in task_graph.json has an output.
  2. Deviation audit — any non-None deviation in a summary is a warning.
  3. File overlap   — any file written by more than one task is an error.
  4. Outstanding    — any non-None outstanding item in a summary is a warning.
  5. Acceptance     — for each AC-F00X in spec/05_acceptance.md, the owning
                      task must have at least one file (presence-only check).

validate() NEVER raises — it always returns a ValidationReport.
report.passed is True iff report.errors == 0.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from devos.synthesis.collector import CollectedOutputs

logger = logging.getLogger(__name__)


# ── Data structures ─────────────────────────────────────────────────────────────


@dataclass
class Finding:
    """A single validator finding.

    Attributes:
        level:    ``"error"`` blocks the merge; ``"warning"`` does not.
        check:    Which check produced this finding (e.g. ``"completeness"``).
        task_id:  Associated task, or None for graph-level checks.
        message:  Human-readable description of the finding.
    """

    level: Literal["error", "warning"]
    check: str
    task_id: str | None
    message: str


@dataclass
class ValidationReport:
    """Aggregated result of all five validation checks.

    Attributes:
        findings: All findings from all checks.
        errors:   Count of level="error" findings.
        warnings: Count of level="warning" findings.
        passed:   True iff errors == 0.
    """

    findings: list[Finding] = field(default_factory=list)
    errors: int = 0
    warnings: int = 0
    passed: bool = True


# ── Main class ──────────────────────────────────────────────────────────────────


class SynthesisValidator:
    """Validates CollectedOutputs against the spec and task graph.

    Args:
        spec_dir:  Path to the ``spec/`` directory.
        devos_dir: Path to the ``.devos/`` runtime directory.
    """

    def __init__(self, spec_dir: Path, devos_dir: Path) -> None:
        self._spec_dir = spec_dir
        self._devos_dir = devos_dir

    def validate(self, outputs: CollectedOutputs) -> ValidationReport:
        """Run all five checks and return a ValidationReport.

        Checks run in order; all checks execute regardless of earlier results.
        The method never raises — individual check failures are caught and
        logged as errors in the report.

        Args:
            outputs: CollectedOutputs produced by OutputCollector.collect().

        Returns:
            ValidationReport with findings, error/warning counts, and passed flag.
        """
        findings: list[Finding] = []

        _check_completeness(outputs, self._devos_dir, findings)
        _check_deviations(outputs, findings)
        _check_overlap(outputs, findings)
        _check_outstanding(outputs, findings)
        _check_acceptance(outputs, self._spec_dir, self._devos_dir, findings)

        errors = sum(1 for f in findings if f.level == "error")
        warnings = sum(1 for f in findings if f.level == "warning")
        return ValidationReport(
            findings=findings,
            errors=errors,
            warnings=warnings,
            passed=(errors == 0),
        )


# ── Individual checks (module-private functions) ────────────────────────────────


def _check_completeness(
    outputs: CollectedOutputs,
    devos_dir: Path,
    findings: list[Finding],
) -> None:
    """Check 1: every task_id in task_graph.json must have a TaskOutput."""
    task_graph_path = devos_dir / "task_graph.json"
    if not task_graph_path.exists():
        findings.append(
            Finding(
                level="error",
                check="completeness",
                task_id=None,
                message="task_graph.json not found — cannot verify completeness",
            )
        )
        return

    try:
        task_graph = json.loads(task_graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(
            Finding(
                level="error",
                check="completeness",
                task_id=None,
                message=f"Could not read task_graph.json: {exc}",
            )
        )
        return

    for wave in task_graph.get("waves", []):
        for task in wave.get("tasks", []):
            task_id = task["id"]
            if task_id not in outputs.task_outputs:
                findings.append(
                    Finding(
                        level="error",
                        check="completeness",
                        task_id=task_id,
                        message=f"Task {task_id} has no output",
                    )
                )


def _check_deviations(
    outputs: CollectedOutputs,
    findings: list[Finding],
) -> None:
    """Check 2: any non-None deviation in a summary is a warning."""
    for task_id, task_output in outputs.task_outputs.items():
        deviations = task_output.summary.deviations
        if deviations is not None:
            for deviation in deviations:
                findings.append(
                    Finding(
                        level="warning",
                        check="deviation",
                        task_id=task_id,
                        message=deviation,
                    )
                )


def _check_overlap(
    outputs: CollectedOutputs,
    findings: list[Finding],
) -> None:
    """Check 3: any file written by more than one task is an error."""
    file_owners: dict[str, list[str]] = {}  # posix path str → [task_ids]
    for task_id, task_output in outputs.task_outputs.items():
        for file_path in task_output.files:
            key = file_path.as_posix()
            file_owners.setdefault(key, []).append(task_id)

    for path_str, task_ids in file_owners.items():
        if len(task_ids) > 1:
            findings.append(
                Finding(
                    level="error",
                    check="overlap",
                    task_id=None,
                    message=f"{path_str} written by {task_ids}",
                )
            )


def _check_outstanding(
    outputs: CollectedOutputs,
    findings: list[Finding],
) -> None:
    """Check 4: any non-None outstanding item in a summary is a warning."""
    for task_id, task_output in outputs.task_outputs.items():
        outstanding = task_output.summary.outstanding
        if outstanding is not None:
            for item in outstanding:
                findings.append(
                    Finding(
                        level="warning",
                        check="outstanding",
                        task_id=task_id,
                        message=item,
                    )
                )


def _check_acceptance(
    outputs: CollectedOutputs,
    spec_dir: Path,
    devos_dir: Path,
    findings: list[Finding],
) -> None:
    """Check 5: presence-only check — each AC must have at least one file.

    For each ``### AC-F00X`` block in spec/05_acceptance.md, finds the task
    that owns the AC via task_graph.json acceptance_ids, then checks that
    the task has at least one file in its TaskOutput.
    """
    # Parse AC identifiers from acceptance spec.
    acceptance_path = spec_dir / "05_acceptance.md"
    if not acceptance_path.exists():
        logger.warning("05_acceptance.md not found — skipping acceptance spot-check")
        return

    ac_ids: list[str] = []
    try:
        for line in acceptance_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("### AC-"):
                # "### AC-F001: User Authentication" → "AC-F001"
                ac_id = stripped[4:].split(":")[0].strip()
                if ac_id:
                    ac_ids.append(ac_id)
    except OSError as exc:
        logger.warning("Could not read 05_acceptance.md: %s", exc)
        return

    # Build AC → task_id mapping from task_graph.
    task_graph_path = devos_dir / "task_graph.json"
    if not task_graph_path.exists():
        return

    try:
        task_graph = json.loads(task_graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read task_graph.json for acceptance check: %s", exc)
        return

    ac_to_task: dict[str, str] = {}
    for wave in task_graph.get("waves", []):
        for task in wave.get("tasks", []):
            for ac_id in task.get("acceptance_ids", []):
                ac_to_task[ac_id] = task["id"]

    # Spot-check each AC.
    for ac_id in ac_ids:
        task_id = ac_to_task.get(ac_id)
        if task_id is None:
            continue  # AC not owned by any task — not a validator concern
        task_output = outputs.task_outputs.get(task_id)
        if task_output is None or not task_output.files:
            # "AC-F001" → fid = "F001"
            fid = ac_id[3:] if ac_id.startswith("AC-") else ac_id
            findings.append(
                Finding(
                    level="warning",
                    check="acceptance",
                    task_id=task_id,
                    message=f"AC-{fid}: no files found for component",
                )
            )
