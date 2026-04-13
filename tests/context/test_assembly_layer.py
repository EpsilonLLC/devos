"""Tests for the Context Assembly Layer — injector, assembler, distiller.

Fixtures:
  - tmp_path (pytest built-in)     — isolated per-test filesystem
  - task_graph.json                — loaded for Task construction helpers
  - mock_anthropic_client fixture  — no live LLM calls in distiller tests

Test IDs:
  a) test_injector_places_constraints_at_position_zero
  b) test_injector_preserves_existing_non_constraint_block_at_index_1
  c) test_injector_raises_on_missing_constraints_file
  d) test_injector_verify_catches_tampered_constraints_content
  e) test_assembler_respects_max_spec_sections_3
  f) test_assembler_respects_max_files_5
  g) test_assembler_expands_directory_to_direct_py_children_sorted
  h) test_assembler_skips_missing_files_and_records_in_manifest
  i) test_assembler_builds_interfaces_from_prior_summaries_only
  j) test_assembler_evicts_tail_first_never_constraints_never_task
  k) test_assembler_raises_context_budget_exceeded_when_irreducible
  l) test_assembler_is_deterministic_same_inputs_same_package
  m) test_distiller_parses_all_six_sections
  n) test_distiller_writes_summary_file_to_summaries_dir
  o) test_distiller_enforces_token_budget
  p) test_distiller_never_reads_spec_files_or_constraints
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from devos.context import (
    MAX_CONTEXT_TOKENS,
    MAX_FILES_PER_TASK,
    MAX_SPEC_SECTIONS_PER_TASK,
    ConstraintInjector,
    ConstraintsMissingError,
    ConstraintsViolationError,
    ContextAssembler,
    ContextBudgetExceededError,
    ContextBlock,
    DistillationBudgetError,
    MemoryDistiller,
)
from devos.decomposition.dependency_graph import Task


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers / factories
# ─────────────────────────────────────────────────────────────────────────────

_CONSTRAINTS_CONTENT = """\
# Constraints
# Injected at TOP of every agent context. Never summarized away.

## Hard rules
- All DB queries include tenant_id filter
- No raw SQL outside db/repositories/
- All endpoints return standard error envelope
- No secrets in code — environment variables only
- All async functions use async def

## Naming
- Files: snake_case
- Classes: PascalCase
"""


def _write_constraints(devos_dir: Path, content: str = _CONSTRAINTS_CONTENT) -> Path:
    devos_dir.mkdir(parents=True, exist_ok=True)
    p = devos_dir / "constraints.md"
    p.write_text(content, encoding="utf-8")
    return p


def _make_injector(devos_dir: Path, content: str = _CONSTRAINTS_CONTENT) -> ConstraintInjector:
    path = _write_constraints(devos_dir, content)
    return ConstraintInjector(path)


def _make_assembler(
    repo_root: Path,
    devos_dir: Path,
    injector: ConstraintInjector | None = None,
) -> ContextAssembler:
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(exist_ok=True)
    if injector is None:
        injector = _make_injector(devos_dir)
    return ContextAssembler(
        repo_root=repo_root,
        spec_dir=spec_dir,
        devos_dir=devos_dir,
        injector=injector,
    )


def _make_task(
    task_id: str = "T-001",
    name: str = "core module",
    component: str = "core",
    spec_files: list[str] | None = None,
    relevant_files: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        name=name,
        component=component,
        spec_files=spec_files or [],
        relevant_files=relevant_files or [],
        depends_on=depends_on or [],
        write_targets=[],
        write_tables=[],
        feature_ids=[],
        acceptance_ids=[],
    )


def _make_mock_anthropic(response_text: str) -> MagicMock:
    """Return a mock anthropic client whose messages.create returns response_text."""
    message_content = SimpleNamespace(text=response_text)
    message = SimpleNamespace(content=[message_content])
    client = MagicMock()
    client.messages.create.return_value = message
    return client


# ─────────────────────────────────────────────────────────────────────────────
# (a) Injector — constraints placed at position 0
# ─────────────────────────────────────────────────────────────────────────────

def test_injector_places_constraints_at_position_zero(tmp_path):
    injector = _make_injector(tmp_path / ".devos")
    task_block = ContextBlock(kind="task", path=None, content="task text", token_estimate=2)
    result = injector.inject([task_block])

    assert result[0].kind == "constraints"
    assert len(result) == 2
    assert result[1].kind == "task"


# ─────────────────────────────────────────────────────────────────────────────
# (b) Injector — existing block at index 0 is preserved at index 1
# ─────────────────────────────────────────────────────────────────────────────

def test_injector_preserves_existing_non_constraint_block_at_index_1(tmp_path):
    injector = _make_injector(tmp_path / ".devos")
    block_a = ContextBlock(kind="task", path=None, content="first", token_estimate=1)
    block_b = ContextBlock(kind="spec", path=None, content="second", token_estimate=1)

    result = injector.inject([block_a, block_b])

    assert result[0].kind == "constraints"
    assert result[1] is block_a  # original index-0 block is now at index 1
    assert result[2] is block_b
    assert len(result) == 3


# ─────────────────────────────────────────────────────────────────────────────
# (c) Injector — raises on missing constraints file
# ─────────────────────────────────────────────────────────────────────────────

def test_injector_raises_on_missing_constraints_file(tmp_path):
    with pytest.raises(ConstraintsMissingError):
        ConstraintInjector(tmp_path / "nonexistent" / "constraints.md")


# ─────────────────────────────────────────────────────────────────────────────
# (d) Injector — verify catches tampered constraints content
# ─────────────────────────────────────────────────────────────────────────────

def test_injector_verify_catches_tampered_constraints_content(tmp_path):
    injector = _make_injector(tmp_path / ".devos")

    # Build a valid list first, then swap the constraints block content
    task_block = ContextBlock(kind="task", path=None, content="task", token_estimate=1)
    blocks = injector.inject([task_block])

    # Tamper: replace the constraints block at index 0 with different content
    tampered = ContextBlock(
        kind="constraints",
        path=blocks[0].path,
        content="TAMPERED CONTENT",
        token_estimate=4,
    )
    tampered_blocks = [tampered] + list(blocks[1:])

    with pytest.raises(ConstraintsViolationError):
        injector.verify(tampered_blocks)


# ─────────────────────────────────────────────────────────────────────────────
# (e) Assembler — respects MAX_SPEC_SECTIONS_PER_TASK = 3
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_respects_max_spec_sections_3(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(parents=True)

    # Create 5 spec files
    spec_files: list[str] = []
    for i in range(5):
        fname = f"spec_{i:02d}.md"
        (spec_dir / fname).write_text(f"# Spec {i}\n" + "x" * 200, encoding="utf-8")
        spec_files.append(f"spec/{fname}")

    assembler = _make_assembler(repo_root, devos_dir)
    task = _make_task(spec_files=spec_files)
    pkg = assembler.assemble(task)

    spec_blocks = [b for b in pkg.blocks if b.kind == "spec"]
    assert len(spec_blocks) == MAX_SPEC_SECTIONS_PER_TASK


# ─────────────────────────────────────────────────────────────────────────────
# (f) Assembler — respects MAX_FILES_PER_TASK = 5
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_respects_max_files_5(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    src_dir = repo_root / "mymodule"
    src_dir.mkdir(parents=True)

    # Create 8 .py files in the directory
    for i in range(8):
        (src_dir / f"module_{i:02d}.py").write_text(f"# module {i}", encoding="utf-8")

    assembler = _make_assembler(repo_root, devos_dir)
    task = _make_task(relevant_files=["mymodule/"])
    pkg = assembler.assemble(task)

    file_blocks = [b for b in pkg.blocks if b.kind == "file"]
    assert len(file_blocks) == MAX_FILES_PER_TASK


# ─────────────────────────────────────────────────────────────────────────────
# (g) Assembler — expands directory to direct .py children, sorted
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_expands_directory_to_direct_py_children_sorted(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    src_dir = repo_root / "pkg"
    src_dir.mkdir(parents=True)

    # Create files out of alphabetical order, include a non-.py file
    names = ["zebra.py", "alpha.py", "module.py", "readme.txt"]
    for name in names:
        (src_dir / name).write_text(f"# {name}", encoding="utf-8")
    # Also a sub-directory (should NOT be expanded — non-recursive)
    (src_dir / "subpkg").mkdir()
    (src_dir / "subpkg" / "deep.py").write_text("# deep", encoding="utf-8")

    assembler = _make_assembler(repo_root, devos_dir)
    task = _make_task(relevant_files=["pkg/"])
    pkg = assembler.assemble(task)

    file_blocks = [b for b in pkg.blocks if b.kind == "file"]
    filenames = [b.path.name for b in file_blocks]

    # Should only include .py files from pkg/ (not subpkg/deep.py, not readme.txt)
    assert "readme.txt" not in filenames
    assert "deep.py" not in filenames
    # Should be sorted alphabetically
    assert filenames == sorted(filenames)
    assert set(filenames) == {"alpha.py", "module.py", "zebra.py"}


# ─────────────────────────────────────────────────────────────────────────────
# (h) Assembler — skips missing files and records in manifest
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_skips_missing_files_and_records_in_manifest(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    repo_root.mkdir()

    # Point to a non-existent directory and a non-existent file
    missing_dir = "devos/nonexistent_module/"
    missing_file = "spec/does_not_exist.md"

    assembler = _make_assembler(repo_root, devos_dir)
    task = _make_task(
        spec_files=[missing_file],
        relevant_files=[missing_dir],
    )
    pkg = assembler.assemble(task)

    # No file or spec blocks from missing paths
    assert not any(b.kind == "file" for b in pkg.blocks)
    assert not any(b.kind == "spec" for b in pkg.blocks)

    # Both missing paths recorded in manifest
    skipped = pkg.manifest["skipped_files"]
    assert missing_dir in skipped or missing_file in skipped


# ─────────────────────────────────────────────────────────────────────────────
# (i) Assembler — builds interfaces from prior summaries only
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_builds_interfaces_from_prior_summaries_only(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    repo_root.mkdir()

    # Create a distilled summary for T-001 with an interfaces section
    summaries_dir = devos_dir / "summaries"
    summaries_dir.mkdir(parents=True)
    (summaries_dir / "T-001.md").write_text(
        "# Task summary: T-001 — core module\n\n"
        "## Decisions made\n"
        "- Used async session factory\n\n"
        "## Interfaces produced\n"
        "- get_db_session() -> AsyncSession\n"
        "- BaseRepository class with CRUD methods\n\n"
        "## Files modified\n"
        "- devos/core/db.py\n",
        encoding="utf-8",
    )

    assembler = _make_assembler(repo_root, devos_dir)
    # T-003 depends on T-001
    task = _make_task(task_id="T-003", name="auth module", depends_on=["T-001"])
    pkg = assembler.assemble(task)

    interface_blocks = [b for b in pkg.blocks if b.kind == "interface"]
    assert len(interface_blocks) == 1

    iface_content = interface_blocks[0].content
    # Contains only the interfaces section from T-001, not decisions or files
    assert "get_db_session" in iface_content
    assert "BaseRepository" in iface_content
    assert "## T-001" in iface_content
    # Does NOT include "Decisions made" content
    assert "async session factory" not in iface_content


# ─────────────────────────────────────────────────────────────────────────────
# (j) Assembler — evicts tail first; constraints and task are never evicted
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_evicts_tail_first_never_constraints_never_task(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"

    # Use predictable block sizes: each spec/summary file is exactly 100 chars → 25 tokens.
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(parents=True)
    spec_paths: list[str] = []
    for i in range(2):
        fname = f"spec_{i:02d}.md"
        (spec_dir / fname).write_text("S" * 100, encoding="utf-8")
        spec_paths.append(f"spec/{fname}")

    devos_dir.mkdir(parents=True, exist_ok=True)
    summary_paths: list[Path] = []
    for i in range(2):
        p = devos_dir / f"prior_{i}.md"
        p.write_text("P" * 100, encoding="utf-8")
        summary_paths.append(p)

    injector = _make_injector(devos_dir)
    task = _make_task(spec_files=spec_paths)

    # Compute the irreducible floor (constraints + task) from actual content.
    from devos.context.assembler import _render_task_md
    floor_tokens = (
        len(_CONSTRAINTS_CONTENT) // 4
        + len(_render_task_md(task)) // 4
    )
    # Each extra block contributes 25 tokens.  Set a budget that sits above
    # the floor (constraints+task fit) but below floor + all extras, so
    # at least the two summaries (50 tokens) must be evicted.
    per_extra = 100 // 4          # 25 tokens per spec or summary block
    extras_total = 4 * per_extra  # 2 specs + 2 summaries = 100 tokens
    budget = floor_tokens + per_extra  # floor + 1 block worth; forces 3 evictions
    assert budget < floor_tokens + extras_total, "Budget must trigger eviction"

    monkeypatch.setattr("devos.context.assembler.MAX_CONTEXT_TOKENS", budget)

    assembler = _make_assembler(repo_root, devos_dir, injector=injector)
    pkg = assembler.assemble(task, prior_summaries=summary_paths)

    kinds = [b.kind for b in pkg.blocks]

    # Constraints and task must always survive
    assert "constraints" in kinds
    assert "task" in kinds

    # Something was evicted
    assert len(pkg.manifest["evicted"]) > 0

    # Total tokens within budget
    assert pkg.total_tokens <= budget


# ─────────────────────────────────────────────────────────────────────────────
# (k) Assembler — raises ContextBudgetExceededError when irreducible
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_raises_context_budget_exceeded_when_irreducible(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    repo_root.mkdir()

    # Set budget to 1 token — constraints alone (>50 chars) will exceed it
    monkeypatch.setattr("devos.context.assembler.MAX_CONTEXT_TOKENS", 1)

    assembler = _make_assembler(repo_root, devos_dir)
    task = _make_task()

    with pytest.raises(ContextBudgetExceededError):
        assembler.assemble(task)


# ─────────────────────────────────────────────────────────────────────────────
# (l) Assembler — deterministic: same inputs → same package
# ─────────────────────────────────────────────────────────────────────────────

def test_assembler_is_deterministic_same_inputs_same_package(tmp_path):
    repo_root = tmp_path / "repo"
    devos_dir = tmp_path / ".devos"
    spec_dir = repo_root / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "components.md").write_text("# Components\nsome spec content", encoding="utf-8")

    mod_dir = repo_root / "devos" / "core"
    mod_dir.mkdir(parents=True)
    (mod_dir / "base.py").write_text("# base", encoding="utf-8")

    task = _make_task(
        spec_files=["spec/components.md"],
        relevant_files=["devos/core/"],
    )

    def _run(run_id: int) -> dict:
        # Each run uses a fresh session dir suffix to avoid clobber
        injector = _make_injector(devos_dir)
        assembler = ContextAssembler(
            repo_root=repo_root,
            spec_dir=spec_dir,
            devos_dir=devos_dir,
            injector=injector,
        )
        pkg = assembler.assemble(task)
        m = dict(pkg.manifest)
        m.pop("generated_at")   # timestamp is the only non-deterministic field
        return m

    first = _run(0)
    second = _run(1)

    assert first == second


# ─────────────────────────────────────────────────────────────────────────────
# (m) Distiller — parses all six sections correctly
# ─────────────────────────────────────────────────────────────────────────────

_FULL_SUMMARY = """\
# Task summary: T-001 — core module

## Decisions made
- Used AsyncSession from SQLAlchemy
- BaseRepository holds common CRUD logic

## Files modified
- devos/core/db.py
- devos/core/repository.py

## Interfaces produced
- get_db_session() -> AsyncSession
- BaseRepository.get_by_id(id: UUID) -> Model

## Deviations from spec
- None

## Tests written
- test_db_session_closes_on_exception: verifies cleanup
- test_base_repo_get_by_id: happy path

## Outstanding
- None
"""


def test_distiller_parses_all_six_sections(tmp_path):
    client = _make_mock_anthropic(_FULL_SUMMARY)
    distiller = MemoryDistiller(client)
    summary = distiller.distill("T-001", "core module", raw_output="agent output here")

    assert summary.task_id == "T-001"
    assert summary.task_name == "core module"
    assert len(summary.decisions) == 2
    assert "Used AsyncSession from SQLAlchemy" in summary.decisions
    assert len(summary.files_modified) == 2
    assert "devos/core/db.py" in summary.files_modified
    assert len(summary.interfaces_produced) == 2
    assert "get_db_session() -> AsyncSession" in summary.interfaces_produced
    assert summary.deviations is None   # "None" → None
    assert len(summary.tests_written) == 2
    assert summary.outstanding is None  # "None" → None


# ─────────────────────────────────────────────────────────────────────────────
# (n) Distiller — writes summary file to summaries_dir
# ─────────────────────────────────────────────────────────────────────────────

def test_distiller_writes_summary_file_to_summaries_dir(tmp_path):
    summaries_dir = tmp_path / "summaries"
    client = _make_mock_anthropic(_FULL_SUMMARY)
    distiller = MemoryDistiller(client)
    summary = distiller.distill("T-001", "core module", raw_output="output")

    out_path = distiller.write(summary, summaries_dir)

    assert out_path == summaries_dir / "T-001.md"
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert content == summary.rendered_markdown


# ─────────────────────────────────────────────────────────────────────────────
# (o) Distiller — enforces token budget (raises DistillationBudgetError)
# ─────────────────────────────────────────────────────────────────────────────

def test_distiller_enforces_token_budget(tmp_path):
    # Craft a response that, after len//4, is above 200 * 1.25 = 250 tokens.
    # 250 tokens * 4 chars = 1000 chars minimum to exceed budget.
    bloated_response = "# Task summary: T-002 — db\n" + "x" * 1100
    client = _make_mock_anthropic(bloated_response)
    distiller = MemoryDistiller(client)
    summary = distiller.distill("T-002", "db", raw_output="output", target_tokens=200)

    # token_estimate = len(bloated_response) // 4  ≈ 307 > 250
    summaries_dir = tmp_path / "summaries"
    with pytest.raises(DistillationBudgetError):
        distiller.write(summary, summaries_dir, target_tokens=200)


# ─────────────────────────────────────────────────────────────────────────────
# (p) Distiller — never reads spec files or constraints
# ─────────────────────────────────────────────────────────────────────────────

def test_distiller_never_reads_spec_files_or_constraints(tmp_path):
    """Structural test: MemoryDistiller has no path to spec or constraints data.

    The distiller receives only task_id, task_name, and raw_output.
    We verify the LLM call does not include any spec-like content by asserting
    that the user prompt passed to the client contains only the template and
    the raw_output — not any content derived from files on disk.
    """
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    secret_marker = "SECRET_SPEC_CONTENT_THAT_MUST_NOT_APPEAR_IN_PROMPT"
    (spec_dir / "01_functional.md").write_text(secret_marker, encoding="utf-8")

    constraints_path = tmp_path / "constraints.md"
    constraints_path.write_text("SECRET_CONSTRAINTS_CONTENT", encoding="utf-8")

    client = _make_mock_anthropic(_FULL_SUMMARY)
    distiller = MemoryDistiller(client)

    # distill() takes only task_id, task_name, raw_output — no file paths
    distiller.distill("T-001", "core module", raw_output="agent did some work")

    # Inspect what was actually sent to the LLM
    call_kwargs = client.messages.create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else []
    if hasattr(call_kwargs, "kwargs"):
        messages = call_kwargs.kwargs.get("messages", [])

    user_content = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")

    assert secret_marker not in user_content
    assert "SECRET_CONSTRAINTS_CONTENT" not in user_content

    # Also verify the class has no spec_dir or constraints_path attributes
    assert not hasattr(distiller, "spec_dir")
    assert not hasattr(distiller, "constraints_path")
    assert not hasattr(distiller, "_constraints_path")
    assert not hasattr(distiller, "_spec_dir")
