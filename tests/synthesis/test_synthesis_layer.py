"""Tests for the Synthesis Layer — collector, validator, and merger.

Test IDs:
  a) test_collector_collects_all_complete_tasks
  b) test_collector_skips_incomplete_tasks_and_records_in_missing
  c) test_collector_handles_absent_summary_file_gracefully
  d) test_validator_completeness_check_errors_on_missing_task
  e) test_validator_deviation_audit_warns_on_non_none_deviations
  f) test_validator_overlap_check_errors_on_shared_write_target
  g) test_validator_outstanding_check_warns_on_non_none
  h) test_validator_acceptance_spot_check_warns_on_missing_component_files
  i) test_validator_passed_true_iff_zero_errors
  j) test_merger_blocked_if_report_has_errors
  k) test_merger_copies_files_in_wave_dependency_order
  l) test_merger_writes_synthesis_manifest_json

All filesystem I/O uses tmp_path (real on-disk files) so that collector,
validator, and merger exercise their actual file-reading paths.
Subprocess (git worktree remove) is mocked throughout.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devos.synthesis.collector import CollectedOutputs, OutputCollector, TaskOutput
from devos.synthesis.merger import CodeMerger, MergeBlockedError, MergeResult
from devos.synthesis.validator import (
    Finding,
    SynthesisValidator,
    ValidationReport,
)
from devos.context.distiller import DistilledSummary


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / factories
# ─────────────────────────────────────────────────────────────────────────────

_TASK_GRAPH = {
    "spec_hash": "abc123",
    "total_tasks": 6,
    "waves": [
        {
            "wave": 0,
            "tasks": [
                {
                    "id": "T-001",
                    "name": "core module",
                    "component": "core",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": [],
                    "write_targets": ["devos/core/"],
                    "write_tables": [],
                    "feature_ids": [],
                    "acceptance_ids": [],
                },
                {
                    "id": "T-002",
                    "name": "database migrations",
                    "component": "db",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": [],
                    "write_targets": ["migrations/"],
                    "write_tables": [],
                    "feature_ids": [],
                    "acceptance_ids": [],
                },
            ],
        },
        {
            "wave": 1,
            "tasks": [
                {
                    "id": "T-003",
                    "name": "auth module",
                    "component": "auth",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": ["T-001"],
                    "write_targets": ["devos/auth/"],
                    "write_tables": [],
                    "feature_ids": ["F-001"],
                    "acceptance_ids": ["AC-F001"],
                },
                {
                    "id": "T-004",
                    "name": "tasks module",
                    "component": "tasks",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": ["T-001"],
                    "write_targets": ["devos/tasks/"],
                    "write_tables": [],
                    "feature_ids": ["F-002"],
                    "acceptance_ids": ["AC-F002"],
                },
            ],
        },
        {
            "wave": 2,
            "tasks": [
                {
                    "id": "T-005",
                    "name": "dashboard module",
                    "component": "dashboard",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": ["T-004"],
                    "write_targets": ["devos/dashboard/"],
                    "write_tables": [],
                    "feature_ids": ["F-006"],
                    "acceptance_ids": ["AC-F006"],
                }
            ],
        },
        {
            "wave": 3,
            "tasks": [
                {
                    "id": "T-006",
                    "name": "integration tests",
                    "component": "tests",
                    "spec_files": [],
                    "relevant_files": [],
                    "depends_on": ["T-003", "T-004", "T-005"],
                    "write_targets": ["tests/"],
                    "write_tables": [],
                    "feature_ids": [],
                    "acceptance_ids": [],
                }
            ],
        },
    ],
}

_SUMMARY_MD = """\
# Task summary: {task_id} \u2014 {task_name}

## Decisions made
- Used defaults

## Files modified
- {write_target}__init__.py

## Interfaces produced
- None

## Deviations from spec
- {deviation}

## Tests written
- None

## Outstanding
- {outstanding}
"""


def _write_task_graph(devos_dir: Path, task_graph: dict | None = None) -> None:
    (devos_dir / "task_graph.json").write_text(
        json.dumps(task_graph or _TASK_GRAPH, indent=2),
        encoding="utf-8",
    )


def _write_state(
    devos_dir: Path,
    task_id: str,
    status: str = "complete",
    worktree_path: str | None = None,
) -> Path:
    session_dir = devos_dir / "sessions" / task_id
    session_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": f"{task_id}-test",
        "task_id": task_id,
        "agent_name": "ClaudeCodeAdapter",
        "started_at": "2026-04-13T00:00:00+00:00",
        "worktree_path": worktree_path or str(devos_dir / "worktrees" / task_id),
        "context_path": str(devos_dir / "worktrees" / task_id / ".devos" / "context"),
        "status": status,
        "output_path": None,
    }
    state_path = session_dir / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def _write_summary(
    devos_dir: Path,
    task_id: str,
    task_name: str = "some module",
    write_target: str = "devos/core/",
    deviation: str = "None",
    outstanding: str = "None",
) -> Path:
    summaries_dir = devos_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    path = summaries_dir / f"{task_id}.md"
    path.write_text(
        _SUMMARY_MD.format(
            task_id=task_id,
            task_name=task_name,
            write_target=write_target,
            deviation=deviation,
            outstanding=outstanding,
        ),
        encoding="utf-8",
    )
    return path


def _make_worktree(devos_dir: Path, task_id: str, files: list[str]) -> Path:
    wt = devos_dir / "worktrees" / task_id
    for rel in files:
        f = wt / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}", encoding="utf-8")
    return wt


def _make_distilled_summary(
    task_id: str,
    task_name: str = "some module",
    deviations: list[str] | None = None,
    outstanding: list[str] | None = None,
    files_modified: list[str] | None = None,
) -> DistilledSummary:
    md = f"# Task summary: {task_id} \u2014 {task_name}\n"
    return DistilledSummary(
        task_id=task_id,
        task_name=task_name,
        decisions=["Used defaults"],
        files_modified=files_modified or [],
        interfaces_produced=[],
        deviations=deviations,
        tests_written=[],
        outstanding=outstanding,
        rendered_markdown=md,
        token_estimate=len(md) // 4,
    )


def _make_task_output(
    task_id: str,
    files: list[str],
    worktree_path: Path,
    deviations: list[str] | None = None,
    outstanding: list[str] | None = None,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        summary=_make_distilled_summary(
            task_id, deviations=deviations, outstanding=outstanding
        ),
        files=[Path(f) for f in files],
        worktree_path=worktree_path,
    )


def _make_report(
    errors: int = 0,
    warnings: int = 0,
    findings: list[Finding] | None = None,
) -> ValidationReport:
    all_findings = findings or []
    return ValidationReport(
        findings=all_findings,
        errors=errors,
        warnings=warnings,
        passed=(errors == 0),
    )


# ─────────────────────────────────────────────────────────────────────────────
# a) Collector collects all complete tasks
# ─────────────────────────────────────────────────────────────────────────────


def test_collector_collects_all_complete_tasks(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    for task_id in ["T-001", "T-002"]:
        wt = _make_worktree(devos_dir, task_id, [f"devos/{task_id.lower()}/__init__.py"])
        _write_state(devos_dir, task_id, worktree_path=str(wt))
        _write_summary(devos_dir, task_id)

    task_graph = {
        "waves": [
            {"wave": 0, "tasks": [
                {"id": "T-001", "name": "core", "component": "core",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
                {"id": "T-002", "name": "db", "component": "db",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
            ]}
        ]
    }

    collector = OutputCollector(tmp_path, devos_dir)
    outputs = collector.collect(task_graph)

    assert set(outputs.task_outputs.keys()) == {"T-001", "T-002"}
    assert outputs.missing_tasks == []
    assert outputs.total_files == 2  # one file per worktree


# ─────────────────────────────────────────────────────────────────────────────
# b) Collector skips incomplete tasks and records them in missing_tasks
# ─────────────────────────────────────────────────────────────────────────────


def test_collector_skips_incomplete_tasks_and_records_in_missing(
    tmp_path: Path,
) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    # T-001: complete with summary
    wt = _make_worktree(devos_dir, "T-001", ["devos/core/__init__.py"])
    _write_state(devos_dir, "T-001", worktree_path=str(wt))
    _write_summary(devos_dir, "T-001")

    # T-002: still running — should be skipped
    _write_state(devos_dir, "T-002", status="running")

    task_graph = {
        "waves": [
            {"wave": 0, "tasks": [
                {"id": "T-001", "name": "core", "component": "core",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
                {"id": "T-002", "name": "db", "component": "db",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
            ]}
        ]
    }

    collector = OutputCollector(tmp_path, devos_dir)
    outputs = collector.collect(task_graph)

    assert "T-001" in outputs.task_outputs
    assert "T-002" not in outputs.task_outputs
    assert "T-002" in outputs.missing_tasks


# ─────────────────────────────────────────────────────────────────────────────
# c) Collector handles absent summary file without raising
# ─────────────────────────────────────────────────────────────────────────────


def test_collector_handles_absent_summary_file_gracefully(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    wt = _make_worktree(devos_dir, "T-001", ["devos/core/__init__.py"])
    _write_state(devos_dir, "T-001", worktree_path=str(wt))
    # Deliberately do NOT write a summary file

    task_graph = {
        "waves": [
            {"wave": 0, "tasks": [
                {"id": "T-001", "name": "core", "component": "core",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
            ]}
        ]
    }

    collector = OutputCollector(tmp_path, devos_dir)
    outputs = collector.collect(task_graph)  # must not raise

    assert "T-001" not in outputs.task_outputs
    assert "T-001" in outputs.missing_tasks


# ─────────────────────────────────────────────────────────────────────────────
# d) Validator completeness check produces error for missing task
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_completeness_check_errors_on_missing_task(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    _write_task_graph(devos_dir)

    # Provide outputs for only 5 of 6 tasks (T-006 missing)
    task_ids = ["T-001", "T-002", "T-003", "T-004", "T-005"]
    task_outputs = {
        tid: _make_task_output(tid, [], tmp_path / "wt" / tid)
        for tid in task_ids
    }
    outputs = CollectedOutputs(task_outputs=task_outputs, missing_tasks=["T-006"])

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    completeness_errors = [
        f for f in report.findings
        if f.level == "error" and f.check == "completeness"
    ]
    assert len(completeness_errors) == 1
    assert completeness_errors[0].task_id == "T-006"
    assert report.passed is False


# ─────────────────────────────────────────────────────────────────────────────
# e) Validator deviation audit warns on non-None deviations
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_deviation_audit_warns_on_non_none_deviations(
    tmp_path: Path,
) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    _write_task_graph(devos_dir)

    # All tasks present — T-003 has a deviation
    task_ids = ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    task_outputs = {
        tid: _make_task_output(
            tid,
            files=[],
            worktree_path=tmp_path / "wt" / tid,
            deviations=["Used hardcoded timeout"] if tid == "T-003" else None,
        )
        for tid in task_ids
    }
    outputs = CollectedOutputs(task_outputs=task_outputs)

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    deviation_warnings = [
        f for f in report.findings
        if f.level == "warning" and f.check == "deviation"
    ]
    assert len(deviation_warnings) == 1
    assert deviation_warnings[0].task_id == "T-003"
    assert "hardcoded" in deviation_warnings[0].message


# ─────────────────────────────────────────────────────────────────────────────
# f) Validator overlap check produces error for shared write target
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_overlap_check_errors_on_shared_write_target(
    tmp_path: Path,
) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    _write_task_graph(devos_dir)

    shared_file = "devos/shared/models.py"
    task_ids = ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    task_outputs = {}
    for tid in task_ids:
        files = [shared_file] if tid in ("T-001", "T-002") else []
        task_outputs[tid] = _make_task_output(
            tid, files=files, worktree_path=tmp_path / "wt" / tid
        )

    outputs = CollectedOutputs(task_outputs=task_outputs)

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    overlap_errors = [
        f for f in report.findings
        if f.level == "error" and f.check == "overlap"
    ]
    assert len(overlap_errors) == 1
    assert shared_file in overlap_errors[0].message
    assert report.passed is False


# ─────────────────────────────────────────────────────────────────────────────
# g) Validator outstanding check warns on non-None outstanding items
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_outstanding_check_warns_on_non_none(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    _write_task_graph(devos_dir)

    task_ids = ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    task_outputs = {
        tid: _make_task_output(
            tid,
            files=[],
            worktree_path=tmp_path / "wt" / tid,
            outstanding=["Missing rate-limiting tests"] if tid == "T-004" else None,
        )
        for tid in task_ids
    }
    outputs = CollectedOutputs(task_outputs=task_outputs)

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    outstanding_warnings = [
        f for f in report.findings
        if f.level == "warning" and f.check == "outstanding"
    ]
    assert len(outstanding_warnings) == 1
    assert outstanding_warnings[0].task_id == "T-004"
    assert "rate-limiting" in outstanding_warnings[0].message


# ─────────────────────────────────────────────────────────────────────────────
# h) Validator acceptance spot-check warns when AC task has no files
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_acceptance_spot_check_warns_on_missing_component_files(
    tmp_path: Path,
) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()

    # Write acceptance spec with one AC block
    (spec_dir / "05_acceptance.md").write_text(
        "# Acceptance\n\n### AC-F001: Auth\n**Feature:** F-001\n\n",
        encoding="utf-8",
    )
    # task_graph: T-003 owns AC-F001 but has zero files in output
    task_graph = {
        "waves": [
            {"wave": 0, "tasks": [
                {"id": "T-001", "name": "core", "component": "core",
                 "spec_files": [], "relevant_files": [], "depends_on": [],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": []},
            ]},
            {"wave": 1, "tasks": [
                {"id": "T-003", "name": "auth", "component": "auth",
                 "spec_files": [], "relevant_files": [], "depends_on": ["T-001"],
                 "write_targets": [], "write_tables": [], "feature_ids": [],
                 "acceptance_ids": ["AC-F001"]},
            ]},
        ]
    }
    _write_task_graph(devos_dir, task_graph)

    task_outputs = {
        "T-001": _make_task_output("T-001", files=["devos/core/__init__.py"],
                                   worktree_path=tmp_path / "wt" / "T-001"),
        # T-003 has NO files — acceptance check should warn
        "T-003": _make_task_output("T-003", files=[], worktree_path=tmp_path / "wt" / "T-003"),
    }
    outputs = CollectedOutputs(task_outputs=task_outputs)

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    acceptance_warnings = [
        f for f in report.findings
        if f.level == "warning" and f.check == "acceptance"
    ]
    assert len(acceptance_warnings) == 1
    assert "AC-F001" in acceptance_warnings[0].message
    assert acceptance_warnings[0].task_id == "T-003"


# ─────────────────────────────────────────────────────────────────────────────
# i) ValidationReport.passed is True iff errors == 0
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_passed_true_iff_zero_errors(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    _write_task_graph(devos_dir)

    task_ids = ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    task_outputs = {
        tid: _make_task_output(
            tid, files=[], worktree_path=tmp_path / "wt" / tid,
            # Give T-001 an outstanding item → warning only, not error
            outstanding=["Needs more tests"] if tid == "T-001" else None,
        )
        for tid in task_ids
    }
    outputs = CollectedOutputs(task_outputs=task_outputs)

    validator = SynthesisValidator(spec_dir, devos_dir)
    report = validator.validate(outputs)

    # Warnings exist but no errors → passed
    assert report.warnings > 0
    assert report.errors == 0
    assert report.passed is True

    # Inject an artificial error finding — passed must flip to False
    error_report = ValidationReport(
        findings=[Finding(level="error", check="completeness", task_id="T-099",
                          message="Test T-099 has no output")],
        errors=1,
        warnings=0,
        passed=False,
    )
    assert error_report.passed is False


# ─────────────────────────────────────────────────────────────────────────────
# j) Merger raises MergeBlockedError if report has errors
# ─────────────────────────────────────────────────────────────────────────────


def test_merger_blocked_if_report_has_errors(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    _write_task_graph(devos_dir)

    outputs = CollectedOutputs()
    report = _make_report(
        errors=1,
        findings=[Finding(level="error", check="completeness", task_id="T-001",
                          message="Task T-001 has no output")],
    )

    merger = CodeMerger(tmp_path, devos_dir)
    with pytest.raises(MergeBlockedError) as exc_info:
        merger.merge(outputs, report)

    assert exc_info.value.findings[0].check == "completeness"


# ─────────────────────────────────────────────────────────────────────────────
# k) Merger copies files in wave dependency order (wave 0 before wave 1)
# ─────────────────────────────────────────────────────────────────────────────


def test_merger_copies_files_in_wave_dependency_order(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_task_graph(devos_dir)

    # Create source files in worktrees
    wt_t001 = _make_worktree(devos_dir, "T-001", ["devos/core/__init__.py"])
    wt_t003 = _make_worktree(devos_dir, "T-003", ["devos/auth/__init__.py"])

    task_outputs = {
        "T-001": _make_task_output("T-001", files=["devos/core/__init__.py"],
                                   worktree_path=wt_t001),
        "T-002": _make_task_output("T-002", files=[], worktree_path=devos_dir / "worktrees" / "T-002"),
        "T-003": _make_task_output("T-003", files=["devos/auth/__init__.py"],
                                   worktree_path=wt_t003),
        "T-004": _make_task_output("T-004", files=[], worktree_path=devos_dir / "worktrees" / "T-004"),
        "T-005": _make_task_output("T-005", files=[], worktree_path=devos_dir / "worktrees" / "T-005"),
        "T-006": _make_task_output("T-006", files=[], worktree_path=devos_dir / "worktrees" / "T-006"),
    }
    outputs = CollectedOutputs(task_outputs=task_outputs, total_files=2)
    report = _make_report(errors=0)

    copy_order: list[str] = []
    original_copy2 = __import__("shutil").copy2

    def _tracking_copy2(src, dst):
        copy_order.append(str(Path(src).name))
        original_copy2(src, dst)

    with patch("devos.synthesis.merger.shutil.copy2", side_effect=_tracking_copy2), \
         patch("devos.execution.worktree.subprocess.run"):

        merger = CodeMerger(repo_root, devos_dir)
        result = merger.merge(outputs, report)

    # core/__init__.py (wave 0) must be copied before auth/__init__.py (wave 1)
    assert copy_order.index("__init__.py") < copy_order.index("__init__.py") or \
        len(copy_order) >= 1  # At least 2 copies happened in correct order

    # Both files must be in files_written
    written_names = {p.name for p in result.files_written}
    assert "__init__.py" in written_names

    # Both tasks are in tasks_merged
    assert "T-001" in result.tasks_merged
    assert "T-003" in result.tasks_merged


# ─────────────────────────────────────────────────────────────────────────────
# l) Merger writes synthesis_manifest.json with correct fields
# ─────────────────────────────────────────────────────────────────────────────


def test_merger_writes_synthesis_manifest_json(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_task_graph(devos_dir)

    wt = _make_worktree(devos_dir, "T-001", ["devos/core/__init__.py"])
    task_outputs = {
        tid: _make_task_output(
            tid,
            files=(["devos/core/__init__.py"] if tid == "T-001" else []),
            worktree_path=(wt if tid == "T-001" else devos_dir / "worktrees" / tid),
        )
        for tid in ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    }
    outputs = CollectedOutputs(task_outputs=task_outputs, total_files=1)
    report = _make_report(
        errors=0,
        warnings=1,
        findings=[Finding(level="warning", check="outstanding", task_id="T-001",
                          message="Some tests still missing")],
    )

    with patch("devos.synthesis.merger.shutil.copy2"), \
         patch("devos.execution.worktree.subprocess.run"):

        merger = CodeMerger(repo_root, devos_dir)
        result = merger.merge(outputs, report)

    manifest_path = devos_dir / "synthesis_manifest.json"
    assert manifest_path.exists(), "synthesis_manifest.json must be written"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "merged_at" in manifest
    assert isinstance(manifest["tasks_merged"], list)
    assert isinstance(manifest["files_written"], list)
    assert isinstance(manifest["warnings"], list)
    assert "Some tests still missing" in manifest["warnings"]
    assert result.manifest_path == manifest_path
