"""Writes spec files to disk from InterviewState."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Data structures (InterviewState and friends live here so every module can
# import from one place without circular imports)
# ---------------------------------------------------------------------------

class Feature(BaseModel):
    id: str                       # F-001, F-002, etc.
    name: str
    status: str                   # included | excluded | deferred
    trigger: str
    preconditions: list[str]
    behavior: list[str]           # Numbered steps
    edge_cases: list[str]
    out_of_scope: list[str]


class Table(BaseModel):
    name: str
    purpose: str
    columns: list[dict]           # name, type, nullable, default, constraint
    indexes: list[str]
    relationships: list[str]
    invariants: list[str]


class Endpoint(BaseModel):
    method: str                   # GET POST PATCH DELETE
    path: str                     # /api/v1/...
    auth_required: bool
    request_body: Optional[dict]
    response_200: dict
    error_responses: list[dict]   # scenario, http_code, code
    side_effects: list[str]
    feature_id: str               # F-001 etc — cross reference


class TechStack(BaseModel):
    backend: str
    frontend: Optional[str]
    database: str
    queue: Optional[str]
    extras: list[str]


@dataclass
class InterviewState:
    idea: str
    vision: str = ""
    problem: str = ""
    users: list[dict] = field(default_factory=list)   # {user, goal, constraint}
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    non_negotiables: list[str] = field(default_factory=list)
    success_criteria: str = ""
    features: list[Feature] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    stack: Optional[TechStack] = None
    constraints: list[str] = field(default_factory=list)
    current_phase: int = 0


# ---------------------------------------------------------------------------
# SpecGenerator
# ---------------------------------------------------------------------------

class SpecGenerator:
    """Writes structured spec files from an InterviewState."""

    def write_functional(self, state: InterviewState, output_dir: Path) -> Path:
        """Write 01_functional.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        path = spec_dir / "01_functional.md"
        path.write_text(self._render_functional(state), encoding="utf-8")
        return path

    def write_product(self, state: InterviewState, output_dir: Path) -> Path:
        """Write 00_product.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)

        path = spec_dir / "00_product.md"
        path.write_text(self._render_product(state), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_product(self, state: InterviewState) -> str:
        lines: list[str] = ["# Product", ""]

        # Vision
        lines += ["## Vision", state.vision or "_Not yet defined_", ""]

        # Problem
        lines += ["## Problem", state.problem or "_Not yet defined_", ""]

        # Users table
        lines += ["## Users"]
        if state.users:
            lines += [
                "| User | Goal | Constraint |",
                "|------|------|------------|",
            ]
            for u in state.users:
                user = u.get("user", "")
                goal = u.get("goal", "")
                constraint = u.get("constraint", "")
                lines.append(f"| {user} | {goal} | {constraint} |")
        else:
            lines.append("_Not yet defined_")
        lines.append("")

        # Scope
        lines += ["## Scope", "### In scope"]
        if state.in_scope:
            for item in state.in_scope:
                lines.append(f"- {item}")
        else:
            lines.append("- _Not yet defined_")
        lines.append("")
        lines.append("### Out of scope")
        if state.out_of_scope:
            for item in state.out_of_scope:
                lines.append(f"- {item}")
        else:
            lines.append("- _Not yet defined_")
        lines.append("")

        # Success criteria
        lines += [
            "## Success criteria",
            state.success_criteria or "_Not yet defined_",
            "",
        ]

        # Non-negotiables
        lines += ["## Non-negotiables"]
        if state.non_negotiables:
            for item in state.non_negotiables:
                lines.append(f"- {item}")
        else:
            lines.append("- _Not yet defined_")
        lines.append("")

        return "\n".join(lines)

    def _render_functional(self, state: InterviewState) -> str:
        import re

        lines: list[str] = [
            "# Functional specification",
            "",
            "## Feature schema",
            "Every feature uses this exact structure.",
            "",
        ]

        for feature in state.features:
            lines += [
                "---",
                f"### {feature.id}: {feature.name}",
                f"**Status:** {feature.status}",
                "**Zone:** locked",
                "",
                f"**Trigger:** {feature.trigger}",
                "",
                "**Preconditions:**",
            ]
            for pre in feature.preconditions:
                lines.append(f"- {pre}")
            lines.append("")
            lines.append("**Behavior:**")
            for i, step in enumerate(feature.behavior, 1):
                # Normalise numbering: strip any existing "N." prefix then re-add
                clean = re.sub(r"^\d+\.\s*", "", step).strip()
                lines.append(f"{i}. {clean}")
            lines.append("")
            lines.append("**Edge cases:**")
            for ec in feature.edge_cases:
                lines.append(f"- {ec}")
            lines.append("")
            lines.append("**Out of scope for this feature:**")
            for oos in feature.out_of_scope:
                lines.append(f"- {oos}")
            lines += ["---", ""]

        return "\n".join(lines)
