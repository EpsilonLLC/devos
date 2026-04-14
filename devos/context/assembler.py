"""Context assembler — builds a surgical, token-budgeted context package per task.

Assembly algorithm (deterministic — same inputs → same package):
  a) Start with an empty block list.
  b) Add the task block (rendered task.md).
  c) Add spec sections (max MAX_SPEC_SECTIONS_PER_TASK = 3).
  d) Add relevant files  (max MAX_FILES_PER_TASK = 5, directories expanded).
  e) Add interfaces block from dependency summaries (§ Interfaces produced only).
  f) Add prior_summaries blocks.
  g) Run ConstraintInjector.inject() → constraints at position 0.
  h) Enforce MAX_CONTEXT_TOKENS: evict from tail (summaries → files → spec[1:]).
  i) Write manifest.json into session_dir.
  j) Call injector.verify() — third and final position-0 defence.

NEVER evict the constraints block.  NEVER evict the task block.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from devos.decomposition.dependency_graph import Task
from devos.context.injector import ConstraintInjector, ContextBlock

logger = logging.getLogger(__name__)

# ── Token budget constants ─────────────────────────────────────────────────────
# Single source of truth re-exported through devos/context/__init__.py.
MAX_SPEC_SECTIONS_PER_TASK: int = 3
MAX_FILES_PER_TASK: int = 5
MAX_EXCHANGES_PER_SESSION: int = 25
MAX_CONTEXT_TOKENS: int = 40_000
DISTILLED_SUMMARY_TARGET: int = 200

# ── Implementation directive ────────────────────────────────────────────────────
# Prepended to every task.md at position 0 so agents begin writing immediately.
# Defined here (single source of truth); re-exported from devos/context/__init__.py.
TASK_DIRECTIVE: str = "Write all files now. Do not ask questions."


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ContextBudgetExceededError(Exception):
    """Raised when the context cannot fit within MAX_CONTEXT_TOKENS after full eviction."""


class SessionDirectoryError(Exception):
    """Raised when the session directory cannot be created."""


# ── Public data structures ─────────────────────────────────────────────────────

@dataclass
class ContextPackage:
    """The assembled, budget-enforced context package for one task.

    Attributes:
        task_id:      Task identifier (e.g. ``T-001``).
        session_dir:  ``.devos/sessions/{task_id}/context/``
        blocks:       Ordered ContextBlocks; index 0 is always the constraints block.
        total_tokens: Sum of ``block.token_estimate`` across all blocks.
        manifest:     The dict that was written as ``manifest.json``.
    """

    task_id: str
    session_dir: Path
    blocks: list[ContextBlock]
    total_tokens: int
    manifest: dict


# ── Main class ─────────────────────────────────────────────────────────────────

class ContextAssembler:
    """Builds a ContextPackage for a given Task.

    Args:
        repo_root:  Root of the project repository (contains ``spec/`` and ``devos/``).
        spec_dir:   Path to the ``spec/`` directory.
        devos_dir:  Path to the ``.devos/`` directory (sessions, summaries, constraints).
        injector:   Initialised ConstraintInjector; provides the constraints block.
    """

    def __init__(
        self,
        repo_root: Path,
        spec_dir: Path,
        devos_dir: Path,
        injector: ConstraintInjector,
    ) -> None:
        self._repo_root = repo_root
        self._spec_dir = spec_dir
        self._devos_dir = devos_dir
        self._injector = injector

    def assemble(
        self,
        task: Task,
        prior_summaries: list[Path] | None = None,
    ) -> ContextPackage:
        """Build and return a ContextPackage for *task*.

        Pure with respect to the Task object and all inputs — no mutation occurs.
        All I/O is scoped to ``.devos/sessions/{task_id}/context/``.

        Args:
            task:             Task dataclass from the dependency graph.
            prior_summaries:  Optional list of pre-selected summary paths to include.
                              The caller is responsible for filtering (e.g. relevance).

        Raises:
            SessionDirectoryError:      Cannot create the session directory.
            ContextBudgetExceededError: Context exceeds MAX_CONTEXT_TOKENS even after
                                        full eviction of summary / file / spec blocks.
        """
        prior_summaries = list(prior_summaries) if prior_summaries else []

        session_dir = self._devos_dir / "sessions" / task.id / "context"
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionDirectoryError(
                f"Cannot create session directory: {session_dir}"
            ) from exc

        blocks: list[ContextBlock] = []
        evicted: list[str] = []
        skipped_files: list[str] = []

        # (b) Task block ──────────────────────────────────────────────────────
        task_content = _render_task_md(task)
        task_path = session_dir / "task.md"
        task_path.write_text(task_content, encoding="utf-8")
        blocks.append(ContextBlock(
            kind="task",
            path=task_path,
            content=task_content,
            token_estimate=len(task_content) // 4,
        ))

        # (c) Spec sections (max MAX_SPEC_SECTIONS_PER_TASK) ──────────────────
        spec_files = list(task.spec_files)
        if len(spec_files) > MAX_SPEC_SECTIONS_PER_TASK:
            logger.warning(
                "Task %s specifies %d spec files; truncating to %d.",
                task.id,
                len(spec_files),
                MAX_SPEC_SECTIONS_PER_TASK,
            )
            spec_files = spec_files[:MAX_SPEC_SECTIONS_PER_TASK]

        spec_sections_dir = session_dir / "spec_sections"
        spec_sections_dir.mkdir(exist_ok=True)

        for sf in spec_files:
            src = self._repo_root / sf
            if not src.exists():
                skipped_files.append(sf)
                continue
            content = src.read_text(encoding="utf-8")
            dest = spec_sections_dir / src.name
            dest.write_text(content, encoding="utf-8")
            blocks.append(ContextBlock(
                kind="spec",
                path=dest,
                content=content,
                token_estimate=len(content) // 4,
            ))

        # (d) Relevant files (max MAX_FILES_PER_TASK) ─────────────────────────
        relevant_files_dir = session_dir / "relevant_files"
        relevant_files_dir.mkdir(exist_ok=True)

        resolved_files: list[Path] = []
        for rf in task.relevant_files:
            p = self._repo_root / rf
            if p.is_dir():
                # Expand to direct .py children, sorted alphabetically.
                resolved_files.extend(sorted(p.glob("*.py")))
            elif p.is_file():
                resolved_files.append(p)
            else:
                skipped_files.append(rf)

        if len(resolved_files) > MAX_FILES_PER_TASK:
            resolved_files = resolved_files[:MAX_FILES_PER_TASK]

        for src in resolved_files:
            content = src.read_text(encoding="utf-8")
            try:
                rel = src.relative_to(self._repo_root)
            except ValueError:
                rel = Path(src.name)
            dest = relevant_files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            blocks.append(ContextBlock(
                kind="file",
                path=dest,
                content=content,
                token_estimate=len(content) // 4,
            ))

        # (e) Interfaces block from dependency summaries ───────────────────────
        summaries_dir = self._devos_dir / "summaries"
        interfaces_sections: list[str] = []

        for dep_id in sorted(task.depends_on):
            summary_path = summaries_dir / f"{dep_id}.md"
            if not summary_path.exists():
                continue
            summary_text = summary_path.read_text(encoding="utf-8")
            iface = _extract_interfaces_section(summary_text)
            if iface:
                interfaces_sections.append(f"## {dep_id}\n{iface}")

        if interfaces_sections:
            interfaces_content = "\n\n".join(interfaces_sections)
            interfaces_path = session_dir / "interfaces.md"
            interfaces_path.write_text(interfaces_content, encoding="utf-8")
            blocks.append(ContextBlock(
                kind="interface",
                path=interfaces_path,
                content=interfaces_content,
                token_estimate=len(interfaces_content) // 4,
            ))

        # (f) Prior summaries ──────────────────────────────────────────────────
        if prior_summaries:
            prior_summaries_dir = session_dir / "prior_summaries"
            prior_summaries_dir.mkdir(exist_ok=True)
            for summary_path in prior_summaries:
                if not summary_path.exists():
                    skipped_files.append(str(summary_path))
                    continue
                content = summary_path.read_text(encoding="utf-8")
                dest = prior_summaries_dir / summary_path.name
                dest.write_text(content, encoding="utf-8")
                blocks.append(ContextBlock(
                    kind="summary",
                    path=dest,
                    content=content,
                    token_estimate=len(content) // 4,
                ))

        # (g) Inject constraints at position 0 ────────────────────────────────
        blocks = self._injector.inject(blocks)

        # (h) Enforce token budget — evict from tail ───────────────────────────
        blocks, evicted = _enforce_budget(blocks, evicted)

        total_tokens = sum(b.token_estimate for b in blocks)
        if total_tokens > MAX_CONTEXT_TOKENS:
            raise ContextBudgetExceededError(
                f"Task {task.id}: context is {total_tokens} tokens after full eviction "
                f"(limit {MAX_CONTEXT_TOKENS}). Constraints + task alone exceed budget."
            )

        # (i) Write manifest.json ──────────────────────────────────────────────
        manifest: dict = {
            "task_id": task.id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_tokens": total_tokens,
            "block_order": [
                {
                    "kind": b.kind,
                    "path": str(b.path) if b.path else None,
                    "tokens": b.token_estimate,
                }
                for b in blocks
            ],
            "evicted": sorted(evicted),
            "skipped_files": sorted(skipped_files),
        }
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        # (j) Verify position-0 invariant — third defence ─────────────────────
        self._injector.verify(blocks)

        return ContextPackage(
            task_id=task.id,
            session_dir=session_dir,
            blocks=blocks,
            total_tokens=total_tokens,
            manifest=manifest,
        )


# ── Private helpers ─────────────────────────────────────────────────────────────

def _render_task_md(task: Task) -> str:
    """Render a human- and agent-readable task.md from a Task dataclass.

    TASK_DIRECTIVE is always the first line so agents begin writing immediately.
    No dynamic content (timestamps etc.) — deterministic for the same Task.
    """
    lines: list[str] = [
        TASK_DIRECTIVE,
        "",
        f"# Task: {task.id} — {task.name}",
        "",
        f"**Component:** {task.component}",
        "",
    ]
    if task.feature_ids:
        lines += [f"**Features:** {', '.join(sorted(task.feature_ids))}", ""]
    if task.acceptance_ids:
        lines += [f"**Acceptance criteria:** {', '.join(sorted(task.acceptance_ids))}", ""]
    if task.depends_on:
        lines += [f"**Depends on:** {', '.join(sorted(task.depends_on))}", ""]
    lines += ["**Write targets:**"]
    for t in task.write_targets:
        lines.append(f"- {t}")
    lines.append("")
    if task.write_tables:
        lines += ["**Write tables:**"]
        for t in task.write_tables:
            lines.append(f"- {t}")
        lines.append("")
    lines += ["**Spec files:**"]
    for f in task.spec_files:
        lines.append(f"- {f}")
    lines.append("")
    lines += ["**Relevant files:**"]
    for f in task.relevant_files:
        lines.append(f"- {f}")
    return "\n".join(lines)


def _extract_interfaces_section(summary_text: str) -> str:
    """Extract only the '## Interfaces produced' section from a distilled summary.

    Returns the section body (without its header), stripped of leading/trailing
    whitespace.  Returns an empty string if the section is absent.
    """
    lines = summary_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.strip() == "## Interfaces produced":
            in_section = True
            continue
        if in_section:
            # Stop at the next level-2 header
            if line.startswith("## "):
                break
            section_lines.append(line)
    return "\n".join(section_lines).strip()


def _enforce_budget(
    blocks: list[ContextBlock],
    evicted: list[str],
) -> tuple[list[ContextBlock], list[str]]:
    """Evict blocks from the tail until under MAX_CONTEXT_TOKENS.

    Eviction order:
        1. ``summary`` blocks  (caller-supplied prior summaries)
        2. ``file`` blocks     (relevant source files)
        3. ``spec`` blocks     (spec sections — but never the first one)

    Constraints and task blocks are NEVER evicted.
    The first spec block is NEVER evicted (agent needs at least one spec anchor).
    """
    evicted = list(evicted)

    def total_tokens(bs: list[ContextBlock]) -> int:
        return sum(b.token_estimate for b in bs)

    if total_tokens(blocks) <= MAX_CONTEXT_TOKENS:
        return blocks, evicted

    result = list(blocks)

    for kind_to_evict in ("summary", "file", "spec"):
        if total_tokens(result) <= MAX_CONTEXT_TOKENS:
            break

        # For spec eviction, protect the first spec block in the list.
        first_spec_idx: int | None = None
        if kind_to_evict == "spec":
            for idx, b in enumerate(result):
                if b.kind == "spec":
                    first_spec_idx = idx
                    break

        # Scan backwards (tail first); pop candidates as we go.
        i = len(result) - 1
        while i >= 0:
            if total_tokens(result) <= MAX_CONTEXT_TOKENS:
                break
            b = result[i]
            if b.kind == kind_to_evict and i != first_spec_idx:
                evicted.append(str(b.path) if b.path else f"<{b.kind}>")
                result.pop(i)
                # Popping at index i only shifts elements at indices > i, which
                # we've already visited in this backwards scan.  first_spec_idx
                # is always < i here (it's near the head), so no adjustment needed.
            i -= 1

    return result, evicted
