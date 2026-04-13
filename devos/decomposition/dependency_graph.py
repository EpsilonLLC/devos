"""Build a dependency graph from a validated ParsedSpec.

Generates one Task per component module (auth, tasks, dashboard, core)
plus one Task each for database migrations and API integration tests.

Parallelism rules (from DEVOS_HANDOFF.md):
  Two tasks can run in parallel iff:
    - Zero shared write targets
    - Zero shared tables in write mode
    - Neither depends on an interface the other produces
"""

from __future__ import annotations

from dataclasses import dataclass

from devos.decomposition.spec_parser import ParsedSpec


@dataclass
class Task:
    id: str                   # T-001, T-002, etc.
    name: str
    component: str            # auth | tasks | dashboard | core | db | tests
    spec_files: list[str]     # max 3 — injected into agent context
    relevant_files: list[str] # max 5 — files the agent reads or writes
    depends_on: list[str]     # task IDs that must complete first
    write_targets: list[str]  # directory paths this task writes
    write_tables: list[str]   # DB tables this task creates/modifies
    feature_ids: list[str]    # F-00X references
    acceptance_ids: list[str] # AC-F00X references


# Static table ownership — drives conflict detection at execution time
_MODULE_WRITE_TABLES: dict[str, list[str]] = {
    "core": [],
    "auth": ["tenants", "users", "sessions"],
    "tasks": ["tasks", "task_tags"],
    "dashboard": [],
    "db": [],      # migrations own all tables by definition
    "tests": [],
}


class DependencyGraph:
    """Holds all tasks and their dependency edges."""

    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks

    @classmethod
    def build(cls, parsed_spec: ParsedSpec) -> "DependencyGraph":
        """Derive tasks from a validated ParsedSpec and return the graph."""
        tasks = _build_tasks(parsed_spec)
        return cls(tasks=tasks)


def _build_tasks(parsed_spec: ParsedSpec) -> list[Task]:
    # Index: feature_id → acceptance IDs
    feature_to_ac: dict[str, list[str]] = {}
    for ac in parsed_spec.acceptance:
        feature_to_ac.setdefault(ac.feature_id, []).append(ac.id)

    # Index: component name → sorted feature IDs
    component_to_features: dict[str, list[str]] = {
        comp.name: sorted(comp.feature_ids)
        for comp in parsed_spec.components
    }

    # Index: component name → sorted acceptance IDs (via owned features)
    component_to_ac: dict[str, list[str]] = {
        comp.name: sorted(
            ac_id
            for fid in comp.feature_ids
            for ac_id in feature_to_ac.get(fid, [])
        )
        for comp in parsed_spec.components
    }

    all_table_names: list[str] = sorted(t.name for t in parsed_spec.tables)
    all_feature_ids: list[str] = sorted(f.id for f in parsed_spec.features)
    all_ac_ids: list[str] = sorted(a.id for a in parsed_spec.acceptance)

    return [
        # ── Wave 0 ────────────────────────────────────────────────────
        Task(
            id="T-001",
            name="core module",
            component="core",
            spec_files=[
                "spec/04_components.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "devos/core/",
            ],
            depends_on=[],
            write_targets=["devos/core/"],
            write_tables=[],
            feature_ids=[],
            acceptance_ids=[],
        ),
        Task(
            id="T-002",
            name="database migrations",
            component="db",
            spec_files=[
                "spec/02_data_model.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "migrations/",
            ],
            depends_on=[],
            write_targets=["migrations/"],
            write_tables=all_table_names,
            feature_ids=[],
            acceptance_ids=[],
        ),
        # ── Wave 1 ────────────────────────────────────────────────────
        Task(
            id="T-003",
            name="auth module",
            component="auth",
            spec_files=[
                "spec/01_functional.md",
                "spec/03_api_contract.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "devos/auth/",
                "spec/01_functional.md",
                "spec/03_api_contract.md",
            ],
            depends_on=["T-001"],
            write_targets=["devos/auth/"],
            write_tables=_MODULE_WRITE_TABLES["auth"],
            feature_ids=component_to_features.get("auth", []),
            acceptance_ids=component_to_ac.get("auth", []),
        ),
        Task(
            id="T-004",
            name="tasks module",
            component="tasks",
            spec_files=[
                "spec/01_functional.md",
                "spec/03_api_contract.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "devos/tasks/",
                "spec/01_functional.md",
                "spec/03_api_contract.md",
            ],
            depends_on=["T-001"],
            write_targets=["devos/tasks/"],
            write_tables=_MODULE_WRITE_TABLES["tasks"],
            feature_ids=component_to_features.get("tasks", []),
            acceptance_ids=component_to_ac.get("tasks", []),
        ),
        # ── Wave 2 ────────────────────────────────────────────────────
        Task(
            id="T-005",
            name="dashboard module",
            component="dashboard",
            spec_files=[
                "spec/01_functional.md",
                "spec/04_components.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "devos/dashboard/",
                "spec/01_functional.md",
                "spec/04_components.md",
            ],
            depends_on=["T-004"],
            write_targets=["devos/dashboard/"],
            write_tables=[],
            feature_ids=component_to_features.get("dashboard", []),
            acceptance_ids=component_to_ac.get("dashboard", []),
        ),
        # ── Wave 3 ────────────────────────────────────────────────────
        Task(
            id="T-006",
            name="integration tests",
            component="tests",
            spec_files=[
                "spec/05_acceptance.md",
                "spec/03_api_contract.md",
                ".devos/constraints.md",
            ],
            relevant_files=[
                "tests/",
                "spec/05_acceptance.md",
                "spec/03_api_contract.md",
            ],
            depends_on=["T-003", "T-004", "T-005"],
            write_targets=["tests/"],
            write_tables=[],
            feature_ids=all_feature_ids,
            acceptance_ids=all_ac_ids,
        ),
    ]
