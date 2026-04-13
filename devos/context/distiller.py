"""Memory distiller — compresses an agent's raw output to a ≤200-token summary.

The distiller:
  - Makes ONE LLM call (fresh context, model=claude-sonnet-4-6).
  - Parses the response with a section-header state machine (no regex on content).
  - Writes .devos/summaries/{task_id}.md.
  - NEVER reads constraints.md or any spec file — only the raw agent output.
  - NEVER produces side effects beyond writing the summary file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Prompt constants ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You compress an agent's work log into a structured summary. Output "
    "ONLY the markdown template filled in. No preamble. No commentary. "
    "Target \u2264200 tokens. Omit empty sections with 'None'."
)

# Template mirrors DEVOS_HANDOFF.md distiller output format exactly.
_SUMMARY_TEMPLATE = """\
# Task summary: {task_id} \u2014 {task_name}

## Decisions made
- ...

## Files modified
- ...

## Interfaces produced
- ...

## Deviations from spec
- None / ...

## Tests written
- ...

## Outstanding
- None / ...
"""

# Section headers as they appear (lowercased for matching).
_SECTION_HEADERS: dict[str, str] = {
    "## decisions made": "decisions",
    "## files modified": "files_modified",
    "## interfaces produced": "interfaces_produced",
    "## deviations from spec": "deviations",
    "## tests written": "tests_written",
    "## outstanding": "outstanding",
}


# ── Exceptions ──────────────────────────────────────────────────────────────────

class DistillationBudgetError(Exception):
    """Raised when the distilled summary exceeds ``target_tokens * 1.25``."""


# ── Public data structures ──────────────────────────────────────────────────────

@dataclass
class DistilledSummary:
    """Structured representation of a distilled task summary.

    Attributes:
        task_id:              Task identifier (e.g. ``T-001``).
        task_name:            Human-readable task name.
        decisions:            Key implementation decisions made.
        files_modified:       Paths of files created or modified.
        interfaces_produced:  Signatures / endpoints that downstream tasks depend on.
        deviations:           Spec deviations (``None`` if clean).
        tests_written:        Test function names and what they cover.
        outstanding:          Incomplete items (``None`` if nothing outstanding).
        rendered_markdown:    The raw markdown returned by the LLM.
        token_estimate:       ``len(rendered_markdown) // 4``.
    """

    task_id: str
    task_name: str
    decisions: list[str]
    files_modified: list[str]
    interfaces_produced: list[str]
    deviations: list[str] | None
    tests_written: list[str]
    outstanding: list[str] | None
    rendered_markdown: str
    token_estimate: int


# ── Main class ──────────────────────────────────────────────────────────────────

class MemoryDistiller:
    """Compresses an agent's raw output into a structured DistilledSummary.

    Args:
        anthropic_client:  Initialised ``anthropic.Anthropic`` client (or a mock).
        model:             Model ID to use for distillation calls.
    """

    def __init__(
        self,
        anthropic_client,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._client = anthropic_client
        self._model = model

    def distill(
        self,
        task_id: str,
        task_name: str,
        raw_output: str,
        target_tokens: int = 200,
    ) -> DistilledSummary:
        """Call the LLM and return a parsed DistilledSummary.

        The LLM receives:
          - System: compression instruction (≤200 tokens, template only).
          - User:   the template with ``<RAW_OUTPUT>`` appended.

        Args:
            task_id:       Task identifier for the summary header.
            task_name:     Task name for the summary header.
            raw_output:    Full text of the agent's work log.
            target_tokens: Soft token target forwarded to the prompt.

        Returns:
            Parsed DistilledSummary with all six sections populated.
        """
        user_prompt = (
            _SUMMARY_TEMPLATE.format(task_id=task_id, task_name=task_name)
            + f"\n<RAW_OUTPUT>\n{raw_output}\n</RAW_OUTPUT>"
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        rendered_markdown: str = response.content[0].text.strip()
        token_estimate = len(rendered_markdown) // 4

        return _parse_summary(task_id, task_name, rendered_markdown, token_estimate)

    def write(
        self,
        summary: DistilledSummary,
        summaries_dir: Path,
        target_tokens: int = 200,
    ) -> Path:
        """Write summary to ``.devos/summaries/{task_id}.md``.

        Args:
            summary:       Distilled summary to persist.
            summaries_dir: Destination directory (created if absent).
            target_tokens: Budget ceiling; enforced at ``target_tokens * 1.25``.

        Returns:
            Absolute path of the written file.

        Raises:
            DistillationBudgetError: If ``summary.token_estimate > target_tokens * 1.25``.
        """
        budget = int(target_tokens * 1.25)
        if summary.token_estimate > budget:
            raise DistillationBudgetError(
                f"Distilled summary for {summary.task_id} is {summary.token_estimate} "
                f"tokens — exceeds {target_tokens} * 1.25 = {budget}. "
                "Caller should retry with a shorter raw_output."
            )
        summaries_dir.mkdir(parents=True, exist_ok=True)
        out_path = summaries_dir / f"{summary.task_id}.md"
        out_path.write_text(summary.rendered_markdown, encoding="utf-8")
        return out_path


# ── Public helpers ──────────────────────────────────────────────────────────────


def load_summary(md_path: Path) -> "DistilledSummary":
    """Parse a previously written summary .md file into a DistilledSummary.

    Does NOT call the LLM — reads and parses the markdown from disk.  The
    header line ``# Task summary: {task_id} — {task_name}`` is used to
    recover the task identifiers; the stem of ``md_path`` is used as a
    fallback for task_id if the header is absent or unparseable.

    Args:
        md_path: Absolute path to a ``.devos/summaries/{task_id}.md`` file.

    Returns:
        Populated DistilledSummary.  Sections absent from the file default
        to empty lists (or None for deviations / outstanding).

    Raises:
        OSError: If the file cannot be read.
    """
    markdown = md_path.read_text(encoding="utf-8")
    token_estimate = len(markdown) // 4

    # Recover task_id and task_name from the header line.
    task_id: str = md_path.stem
    task_name: str = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Task summary:"):
            rest = stripped[len("# Task summary:"):].strip()
            if " \u2014 " in rest:
                task_id, task_name = rest.split(" \u2014 ", 1)
                task_id = task_id.strip()
                task_name = task_name.strip()
            break

    return _parse_summary(task_id, task_name, markdown, token_estimate)


# ── Private helpers ─────────────────────────────────────────────────────────────

def _parse_summary(
    task_id: str,
    task_name: str,
    rendered_markdown: str,
    token_estimate: int,
) -> DistilledSummary:
    """Parse rendered markdown into a DistilledSummary.

    Uses a section-header state machine.  Pattern matching operates only on
    headers (line starts with ``## ``); content lines are never inspected with
    regex — they are collected verbatim.
    """
    buckets: dict[str, list[str]] = {key: [] for key in _SECTION_HEADERS.values()}
    current_section: str | None = None

    for line in rendered_markdown.splitlines():
        lowered = line.strip().lower()
        if lowered in _SECTION_HEADERS:
            current_section = _SECTION_HEADERS[lowered]
            continue
        if current_section is not None and line.startswith("- "):
            item = line[2:].strip()
            if item:
                buckets[current_section].append(item)

    def _maybe_none(items: list[str]) -> list[str] | None:
        if not items or (len(items) == 1 and items[0].strip().lower() == "none"):
            return None
        return items

    return DistilledSummary(
        task_id=task_id,
        task_name=task_name,
        decisions=buckets["decisions"],
        files_modified=buckets["files_modified"],
        interfaces_produced=buckets["interfaces_produced"],
        deviations=_maybe_none(buckets["deviations"]),
        tests_written=buckets["tests_written"],
        outstanding=_maybe_none(buckets["outstanding"]),
        rendered_markdown=rendered_markdown,
        token_estimate=token_estimate,
    )
