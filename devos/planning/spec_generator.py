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
    out_of_scope: list[str] = []


class Endpoint(BaseModel):
    method: str                   # GET POST PATCH DELETE
    path: str                     # /api/v1/...
    auth_required: bool
    purpose: str = ""
    request_body: Optional[dict]
    response_200: dict
    error_responses: list[dict]   # scenario, http_code, code
    side_effects: list[str]
    feature_id: str               # F-001 etc — cross reference
    out_of_scope: list[str] = []


class TechStack(BaseModel):
    backend: str
    frontend: Optional[str]
    database: str
    queue: Optional[str]
    extras: list[str]


class Component(BaseModel):
    name: str                       # module directory name, e.g. "auth"
    responsibility: str             # one sentence
    owns: str                       # domain owned (for ownership table)
    must_not: str                   # violation to avoid (for ownership table)
    features: list[str]            # F-00X IDs this module owns
    internal_structure: list[str]  # directory tree lines
    interfaces_exposed: list[str]  # what other modules may import
    may_import: list[str]
    must_never_import: list[str]


class ArchConstraints(BaseModel):
    hard_rules: list[str]
    naming: list[str]
    always_used: list[str]
    non_functional: list[str] = []


class PytestStub(BaseModel):
    name: str        # e.g. test_signup_rejects_duplicate_email
    docstring: str   # one-line description of what this test verifies


class EdgeCaseMapping(BaseModel):
    edge_case: str   # exact text from feature's edge_cases list
    test: str        # pytest function name that covers this case


class AcceptanceCriteria(BaseModel):
    feature_id: str                         # F-001 … F-007
    feature_name: str
    done_criteria: list[str]                # Numbered, verifiable conditions
    pytest_stubs: list[PytestStub]
    edge_case_coverage: list[EdgeCaseMapping]


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
    components: list[Component] = field(default_factory=list)
    arch_constraints: Optional[ArchConstraints] = None
    acceptance_criteria: list[AcceptanceCriteria] = field(default_factory=list)
    current_phase: int = 0


# ---------------------------------------------------------------------------
# SpecGenerator
# ---------------------------------------------------------------------------

class SpecGenerator:
    """Writes structured spec files from an InterviewState."""

    def write_acceptance(self, state: InterviewState, output_dir: Path) -> Path:
        """Write spec/05_acceptance.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        path = spec_dir / "05_acceptance.md"
        path.write_text(self._render_acceptance(state), encoding="utf-8")
        return path

    def write_components(self, state: InterviewState, output_dir: Path) -> Path:
        """Write spec/04_components.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        path = spec_dir / "04_components.md"
        path.write_text(self._render_components(state), encoding="utf-8")
        return path

    def write_constraints(self, state: InterviewState, output_dir: Path) -> Path:
        """Write .devos/constraints.md and return its path.

        This file is injected at position 0 in every future agent context window.
        It must stay short (max 20 lines of constraint content) and declarative.
        """
        devos_dir = output_dir / ".devos"
        devos_dir.mkdir(parents=True, exist_ok=True)
        path = devos_dir / "constraints.md"
        path.write_text(self._render_constraints(state), encoding="utf-8")
        return path

    def write_data_model(self, state: InterviewState, output_dir: Path) -> Path:
        """Write spec/02_data_model.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        path = spec_dir / "02_data_model.md"
        path.write_text(self._render_data_model(state), encoding="utf-8")
        return path

    def write_api_contract(self, state: InterviewState, output_dir: Path) -> Path:
        """Write spec/03_api_contract.md and return its path."""
        spec_dir = output_dir / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        path = spec_dir / "03_api_contract.md"
        path.write_text(self._render_api_contract(state), encoding="utf-8")
        return path

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

    def _render_acceptance(self, state: InterviewState) -> str:
        lines: list[str] = ["# Acceptance criteria", ""]

        for ac in state.acceptance_criteria:
            # Strip any leading "F-00X: " prefix from feature_id if already there
            fid = ac.feature_id  # e.g. "F-001"
            # Derive numeric suffix for AC label: AC-F001, AC-F002, etc.
            ac_label = f"AC-{fid.replace('-', '')}"  # AC-F001

            lines += [
                "---",
                f"### {ac_label}: {ac.feature_name}",
                f"**Feature:** {fid}",
                "**Status:** required",
                "",
                "**Done criteria:**",
            ]
            for criterion in ac.done_criteria:
                # Ensure each criterion starts with "- [ ]"
                clean = criterion.lstrip("0123456789.- \t")
                lines.append(f"- [ ] {clean}")
            lines.append("")

            lines.append("**Behavioral tests:**")
            lines.append("```python")
            for stub in ac.pytest_stubs:
                lines.append(f'def {stub.name}():')
                lines.append(f'    """{stub.docstring}"""')
                lines.append("    ...")
                lines.append("")
            lines.append("```")
            lines.append("")

            lines.append("**Edge case coverage required:**")
            if ac.edge_case_coverage:
                lines.append("| Edge case | Test name |")
                lines.append("|-----------|-----------|")
                for mapping in ac.edge_case_coverage:
                    ec = mapping.edge_case.replace("|", "\\|")
                    test = mapping.test
                    lines.append(f"| {ec} | `{test}` |")
            else:
                lines.append("- _(no edge cases defined for this feature)_")
            lines += ["", "---", ""]

        return "\n".join(lines)

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

    def _render_data_model(self, state: InterviewState) -> str:
        import json

        lines: list[str] = [
            "# Data model",
            "",
            "## Conventions (locked — agents must follow exactly)",
            "- All tables: snake_case",
            "- All PKs: `id UUID DEFAULT gen_random_uuid()`",
            "- All tables: `created_at TIMESTAMPTZ DEFAULT NOW()`",
            "- All tables: `updated_at TIMESTAMPTZ DEFAULT NOW()`",
            "- Soft deletes: `deleted_at TIMESTAMPTZ NULL`",
            "- Tenant scoping: every table except `tenants` has `tenant_id UUID NOT NULL`",
            "",
            "## Tables",
            "",
        ]

        for table in state.tables:
            lines.append("---")
            lines.append(f"### `{table.name}`")
            lines.append(f"**Purpose:** {table.purpose}")
            lines.append("")

            lines.append("| Column | Type | Nullable | Default | Constraint |")
            lines.append("|--------|------|----------|---------|------------|")
            for col in table.columns:
                name = col.get("name", "")
                col_type = col.get("type", "")
                nullable = "YES" if col.get("nullable", True) else "NO"
                default = col.get("default") or ""
                constraint = col.get("constraint") or ""
                lines.append(
                    f"| {name} | {col_type} | {nullable} | {default} | {constraint} |"
                )
            lines.append("")

            lines.append("**Indexes:**")
            if table.indexes:
                for idx in table.indexes:
                    lines.append(f"- {idx}")
            else:
                lines.append("- None beyond primary key")
            lines.append("")

            lines.append("**Relationships:**")
            if table.relationships:
                for rel in table.relationships:
                    lines.append(f"- {rel}")
            else:
                lines.append("- None")
            lines.append("")

            lines.append("**Invariants:**")
            if table.invariants:
                for inv in table.invariants:
                    lines.append(f"- {inv}")
            else:
                lines.append("- None")
            lines.append("")

            if table.out_of_scope:
                lines.append("**Out of scope:**")
                for oos in table.out_of_scope:
                    lines.append(f"- {oos}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _render_components(self, state: InterviewState) -> str:
        lines: list[str] = ["# Component architecture", ""]

        # Module ownership table
        lines += [
            "## Module ownership (locked — no cross-module direct access)",
            "| Module | Owns | Must not |",
            "|--------|------|----------|",
        ]
        for comp in state.components:
            lines.append(f"| `{comp.name}/` | {comp.owns} | {comp.must_not} |")
        lines.append("")

        # Patterns section — derived from arch_constraints.always_used
        lines += ["## Patterns (guided)"]
        if state.arch_constraints and state.arch_constraints.always_used:
            for pattern in state.arch_constraints.always_used:
                lines.append(f"- {pattern}")
        else:
            lines += [
                "- Repository pattern: All DB access through repository classes",
                "- Service layer: Business logic in service classes, never in routes",
                "- Dependency injection: Constructor args, no global state",
            ]
        lines.append("")

        # Per-module sections
        for comp in state.components:
            lines.append("---")
            lines.append(f"### Module: `{comp.name}/`")
            lines.append(f"**Responsibility:** {comp.responsibility}")
            lines.append("")

            if comp.features:
                lines.append(f"**Features owned:** {', '.join(comp.features)}")
                lines.append("")

            lines.append("**Internal structure (guided):**")
            lines.append("```")
            for line in comp.internal_structure:
                lines.append(line)
            lines.append("```")
            lines.append("")

            lines.append("**Interfaces exposed:**")
            if comp.interfaces_exposed:
                for iface in comp.interfaces_exposed:
                    lines.append(f"- `{iface}`")
            else:
                lines.append("- _(none — internal module only)_")
            lines.append("")

            lines.append("**May import:**")
            if comp.may_import:
                for imp in comp.may_import:
                    lines.append(f"- `{imp}`")
            else:
                lines.append("- _(none)_")
            lines.append("")

            lines.append("**Must never import:**")
            if comp.must_never_import:
                for imp in comp.must_never_import:
                    lines.append(f"- `{imp}`")
            else:
                lines.append("- _(no restrictions beyond ownership table)_")
            lines += ["---", ""]

        return "\n".join(lines)

    def _render_constraints(self, state: InterviewState) -> str:
        lines: list[str] = [
            "# Constraints",
            "# Injected at TOP of every agent context. Never summarized away.",
            "",
        ]

        # Stack section
        if state.stack:
            lines += ["## Stack"]
            lines.append(f"- Backend: {state.stack.backend}")
            lines.append(f"- Database: {state.stack.database}")
            if state.stack.frontend:
                lines.append(f"- Frontend: {state.stack.frontend}")
            if state.stack.queue:
                lines.append(f"- Queue: {state.stack.queue}")
            for extra in state.stack.extras:
                lines.append(f"- {extra}")
            lines.append("")

        ac = state.arch_constraints

        # Hard rules
        lines += ["## Hard rules — violation fails validation"]
        if ac and ac.hard_rules:
            for rule in ac.hard_rules:
                lines.append(f"- {rule}")
        lines.append("")

        # Naming
        lines += ["## Naming"]
        if ac and ac.naming:
            for rule in ac.naming:
                lines.append(f"- {rule}")
        lines.append("")

        # Always used
        lines += ["## Always used"]
        if ac and ac.always_used:
            for rule in ac.always_used:
                lines.append(f"- {rule}")
        lines.append("")

        # Non-functional (only if present — keeps file short)
        if ac and ac.non_functional:
            lines += ["## Non-functional"]
            for rule in ac.non_functional:
                lines.append(f"- {rule}")
            lines.append("")

        return "\n".join(lines)

    def _render_api_contract(self, state: InterviewState) -> str:
        import json

        lines: list[str] = [
            "# API contract",
            "",
            "## Conventions (locked)",
            "- Base path: `/api/v1`",
            "- Auth: Bearer token in Authorization header (all unless marked [public])",
            "- All requests: Content-Type: application/json",
            "- Timestamps: ISO 8601 UTC",
            "- IDs: UUID strings",
            "",
            "## Error envelope (locked — every error response)",
            "```json",
            "{",
            '  "error": "human_readable_message",',
            '  "code": "MACHINE_READABLE_CODE",',
            '  "detail": {}',
            "}",
            "```",
            "",
            "## Standard error codes",
            "| HTTP | Code | Meaning |",
            "|------|------|---------|",
            "| 400 | VALIDATION_ERROR | Request body invalid |",
            "| 401 | UNAUTHORIZED | Missing or invalid token |",
            "| 403 | FORBIDDEN | Insufficient permission |",
            "| 404 | NOT_FOUND | Resource does not exist |",
            "| 409 | CONFLICT | State conflict |",
            "| 422 | UNPROCESSABLE | Valid syntax, invalid semantics |",
            "| 429 | RATE_LIMITED | Too many requests |",
            "| 500 | INTERNAL_ERROR | Server fault |",
            "",
        ]

        for ep in state.endpoints:
            auth_note = "" if ep.auth_required else " [public]"
            lines.append("---")
            lines.append(f"### `{ep.method} {ep.path}`{auth_note}")
            if ep.purpose:
                lines.append(f"**Purpose:** {ep.purpose}")
            lines.append(f"**Feature:** {ep.feature_id}")
            lines.append("")

            if ep.request_body:
                lines.append("**Request body:**")
                lines.append("```json")
                lines.append(json.dumps(ep.request_body, indent=2))
                lines.append("```")
            else:
                lines.append("**Request body:** None")
            lines.append("")

            lines.append("**Response 200:**")
            lines.append("```json")
            lines.append(json.dumps(ep.response_200, indent=2))
            lines.append("```")
            lines.append("")

            lines.append("**Response errors:**")
            if ep.error_responses:
                lines.append("| Scenario | HTTP | Code |")
                lines.append("|----------|------|------|")
                for err in ep.error_responses:
                    scenario = err.get("scenario", "")
                    http_code = err.get("http_code", "")
                    code = err.get("code", "")
                    lines.append(f"| {scenario} | {http_code} | {code} |")
            else:
                lines.append("| None beyond standard codes | — | — |")
            lines.append("")

            lines.append("**Side effects:**")
            if ep.side_effects:
                for se in ep.side_effects:
                    lines.append(f"- {se}")
            else:
                lines.append("- None")
            lines.append("")

            if ep.out_of_scope:
                lines.append("**Out of scope:**")
                for oos in ep.out_of_scope:
                    lines.append(f"- {oos}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)
