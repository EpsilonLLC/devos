"""devos.synthesis — Synthesis layer: collect, validate, and merge.

Public API:
    OutputCollector    Gathers all worktree outputs after wave execution
    SynthesisValidator Validates outputs against the spec (5 checks)
    CodeMerger         Copies worktree files into the repo root

    CollectedOutputs   Result of OutputCollector.collect()
    TaskOutput         Per-task collected data (summary + files + worktree path)
    ValidationReport   Aggregated validation result (findings, errors, warnings)
    Finding            A single validator finding (level, check, task_id, message)
    MergeResult        Result of CodeMerger.merge()

    MergeBlockedError  Raised by merger when validation has errors
    MergerError        Raised by merger on I/O failures
"""

from devos.synthesis.collector import (  # noqa: F401
    CollectedOutputs,
    OutputCollector,
    TaskOutput,
)
from devos.synthesis.merger import (  # noqa: F401
    CodeMerger,
    MergeBlockedError,
    MergeResult,
    MergerError,
)
from devos.synthesis.validator import (  # noqa: F401
    Finding,
    SynthesisValidator,
    ValidationReport,
)

__all__ = [
    # Collector
    "OutputCollector",
    "CollectedOutputs",
    "TaskOutput",
    # Validator
    "SynthesisValidator",
    "ValidationReport",
    "Finding",
    # Merger
    "CodeMerger",
    "MergeResult",
    "MergeBlockedError",
    "MergerError",
]
