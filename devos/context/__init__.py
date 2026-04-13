"""devos.context — Context Assembly Layer.

Constants (single source of truth — defined here, re-exported by submodules):

    MAX_SPEC_SECTIONS_PER_TASK = 3
    MAX_FILES_PER_TASK         = 5
    MAX_EXCHANGES_PER_SESSION  = 25
    MAX_CONTEXT_TOKENS         = 40_000
    DISTILLED_SUMMARY_TARGET   = 200

Public API:
    ConstraintInjector, ContextBlock
    ContextAssembler,   ContextPackage
    MemoryDistiller,    DistilledSummary
    ConstraintsMissingError, ConstraintsViolationError
    ContextBudgetExceededError, SessionDirectoryError
    DistillationBudgetError
"""

# ── Hard limits (defined here; submodules import from assembler.py) ────────────
# NOTE: assembler.py also defines these names at module level so that tests can
# monkeypatch devos.context.assembler.MAX_CONTEXT_TOKENS cleanly.  The values
# below serve as the public, canonical reference.

MAX_SPEC_SECTIONS_PER_TASK: int = 3
MAX_FILES_PER_TASK: int = 5
MAX_EXCHANGES_PER_SESSION: int = 25
MAX_CONTEXT_TOKENS: int = 40_000
DISTILLED_SUMMARY_TARGET: int = 200

# ── Public classes and exceptions ──────────────────────────────────────────────
from devos.context.injector import (  # noqa: E402
    ConstraintInjector,
    ContextBlock,
    ConstraintsMissingError,
    ConstraintsViolationError,
)
from devos.context.assembler import (  # noqa: E402
    ContextAssembler,
    ContextPackage,
    ContextBudgetExceededError,
    SessionDirectoryError,
)
from devos.context.distiller import (  # noqa: E402
    MemoryDistiller,
    DistilledSummary,
    DistillationBudgetError,
    load_summary,
)

__all__ = [
    # Constants
    "MAX_SPEC_SECTIONS_PER_TASK",
    "MAX_FILES_PER_TASK",
    "MAX_EXCHANGES_PER_SESSION",
    "MAX_CONTEXT_TOKENS",
    "DISTILLED_SUMMARY_TARGET",
    # Injector
    "ConstraintInjector",
    "ContextBlock",
    "ConstraintsMissingError",
    "ConstraintsViolationError",
    # Assembler
    "ContextAssembler",
    "ContextPackage",
    "ContextBudgetExceededError",
    "SessionDirectoryError",
    # Distiller
    "MemoryDistiller",
    "DistilledSummary",
    "DistillationBudgetError",
    "load_summary",
]
