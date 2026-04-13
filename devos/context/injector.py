"""Constraint injection — guarantees .devos/constraints.md is always at position 0.

The position-0 invariant is enforced in three places (defense in depth):
  1. ConstraintInjector.inject  — prepends the constraints block
  2. ConstraintInjector.verify  — asserts the invariant post-hoc
  3. ContextAssembler.assemble  — calls verify() after building the package

constraints.md is NEVER summarised. NEVER omitted. NEVER moved from index 0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_CONSTRAINTS_MIN_CHARS = 50


class ConstraintsMissingError(Exception):
    """Raised when constraints.md is absent or shorter than the minimum length."""


class ConstraintsViolationError(Exception):
    """Raised when the position-0 invariant is violated."""


@dataclass
class ContextBlock:
    """A single unit of context delivered to an agent.

    Attributes:
        kind:           Semantic role of the block.
        path:           Absolute path where the block is stored on disk (or None
                        for synthetic blocks that have no file backing).
        content:        Full text content of the block.
        token_estimate: Approximation: len(content) // 4.  Fast, deterministic,
                        tolerates ±15% error — no tiktoken dependency required.
    """

    kind: Literal["constraints", "task", "spec", "file", "interface", "summary"]
    path: Path | None
    content: str
    token_estimate: int


class ConstraintInjector:
    """Reads constraints.md once and guarantees it occupies position 0 in every
    context package.

    Args:
        constraints_path: Path to .devos/constraints.md.

    Raises:
        ConstraintsMissingError: If the file is absent or contains fewer than
            ``_CONSTRAINTS_MIN_CHARS`` characters (guards against empty writes).
    """

    def __init__(self, constraints_path: Path) -> None:
        if not constraints_path.exists():
            raise ConstraintsMissingError(
                f"constraints.md not found: {constraints_path}"
            )
        content = constraints_path.read_text(encoding="utf-8")
        if len(content) < _CONSTRAINTS_MIN_CHARS:
            raise ConstraintsMissingError(
                f"constraints.md too short ({len(content)} chars, "
                f"minimum {_CONSTRAINTS_MIN_CHARS}): {constraints_path}"
            )
        self._constraints_path = constraints_path
        self._constraints_content = content
        self._constraints_block = ContextBlock(
            kind="constraints",
            path=constraints_path,
            content=content,
            token_estimate=len(content) // 4,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject(self, context_blocks: list[ContextBlock]) -> list[ContextBlock]:
        """Return a new list with the constraints block guaranteed at index 0.

        If the caller already placed a block at index 0, that block is shifted
        to index 1.  Constraints always win position 0.

        Post-condition (asserted): ``result[0].kind == "constraints"``.
        """
        result: list[ContextBlock] = [self._constraints_block] + list(context_blocks)
        assert result[0].kind == "constraints", (
            f"inject post-condition violated: result[0].kind={result[0].kind!r}"
        )
        return result

    def verify(self, blocks: list[ContextBlock]) -> None:
        """Assert the position-0 invariant against a finalised block list.

        Raises:
            ConstraintsViolationError: If any of the following are true:
                - ``blocks`` is empty.
                - ``blocks[0].kind != "constraints"``.
                - ``blocks[0].content`` differs from the source constraints.md.
                - Any block after index 0 has ``kind == "constraints"``.
        """
        if not blocks:
            raise ConstraintsViolationError("Block list is empty — no constraints block.")

        if blocks[0].kind != "constraints":
            raise ConstraintsViolationError(
                f"Position-0 invariant violated: "
                f"blocks[0].kind={blocks[0].kind!r}, expected 'constraints'."
            )

        if blocks[0].content != self._constraints_content:
            raise ConstraintsViolationError(
                "Position-0 constraints block content differs from "
                f"source {self._constraints_path}."
            )

        for i, block in enumerate(blocks[1:], start=1):
            if block.kind == "constraints":
                raise ConstraintsViolationError(
                    f"Duplicate constraints block found at index {i}."
                )
