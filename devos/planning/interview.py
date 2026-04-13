"""Phase 1–5 interview state machine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text

from devos.planning.spec_generator import InterviewState, SpecGenerator
from devos.planning.planning_agent import PlanningAgent

console = Console(highlight=False, force_terminal=True, legacy_windows=False)

_MIN_IDEA_LEN = 10
_MAX_IDEA_LEN = 500
_MAX_RETRIES = 3
_MAX_FEATURES = 8


# ---------------------------------------------------------------------------
# Shared persistence helper
# ---------------------------------------------------------------------------

def _persist_state(state: InterviewState, devos_dir: Path) -> None:
    """Serialize InterviewState to .devos/interview_state.json."""
    path = devos_dir / "interview_state.json"
    payload = {
        "idea": state.idea,
        "vision": state.vision,
        "problem": state.problem,
        "users": state.users,
        "in_scope": state.in_scope,
        "out_of_scope": state.out_of_scope,
        "non_negotiables": state.non_negotiables,
        "success_criteria": state.success_criteria,
        "constraints": state.constraints,
        "current_phase": state.current_phase,
        "features": [f.model_dump() for f in state.features],
        "tables": [t.model_dump() for t in state.tables],
        "endpoints": [e.model_dump() for e in state.endpoints],
        "stack": state.stack.model_dump() if state.stack else None,
        "components": [c.model_dump() for c in state.components],
        "arch_constraints": state.arch_constraints.model_dump() if state.arch_constraints else None,
        "acceptance_criteria": [ac.model_dump() for ac in state.acceptance_criteria],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------

class Phase1_Capture:
    """Capture the product idea and write 00_product.md."""

    name = "phase-1-capture"

    def __init__(self, state: InterviewState) -> None:
        self._state = state
        self._agent = PlanningAgent()
        self._gen = SpecGenerator()

    def run(self) -> InterviewState:
        console.print(
            Panel(
                "[bold cyan]Phase 1 — Capture[/bold cyan]\n"
                "We'll clarify your idea and write the product spec.",
                title="[bold]DevOS[/bold]",
                border_style="cyan",
            )
        )

        # ---- Call planning agent ----------------------------------------
        console.print()
        with Live(
            Text("  Thinking...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            data = self._agent.run_phase1(self._state.idea)

        # ---- Populate state -----------------------------------------------
        self._state.vision = data["vision"]
        self._state.problem = data["problem"]
        self._state.users = data["users"]
        self._state.in_scope = data["in_scope"]
        self._state.out_of_scope = data["out_of_scope"]
        self._state.success_criteria = data["success_criteria"]
        self._state.non_negotiables = data["non_negotiables"]

        # ---- Show summary to user -----------------------------------------
        self._print_summary()

        # ---- Ask for confirmation -----------------------------------------
        try:
            confirmed = Confirm.ask(
                "\n[bold]Does this look right?[/bold]", default=True
            )
        except EOFError:
            confirmed = True  # non-interactive: accept the draft

        if not confirmed:
            console.print(
                Panel(
                    "[yellow]No problem — edit [bold]spec/00_product.md[/bold] "
                    "directly after the phase completes, then re-run.[/yellow]",
                    border_style="yellow",
                )
            )

        # ---- Write spec file ---------------------------------------------
        output_dir = Path.cwd()
        with Live(
            Text("  Writing spec/00_product.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            spec_path = self._gen.write_product(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{spec_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        self._state.current_phase = 1
        console.print("\n[bold green]Phase 1 complete.[/bold green]\n")
        return self._state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _print_summary(self) -> None:
        s = self._state
        lines = [
            f"[bold]Vision:[/bold] {s.vision}",
            "",
            f"[bold]Problem:[/bold] {s.problem}",
            "",
            "[bold]In scope:[/bold]",
        ]
        for item in s.in_scope:
            lines.append(f"  - {item}")
        lines.append("")
        lines.append("[bold]Out of scope:[/bold]")
        for item in s.out_of_scope:
            lines.append(f"  - {item}")
        lines.append("")
        lines.append(f"[bold]Success:[/bold] {s.success_criteria}")

        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Product spec draft[/bold cyan]",
                border_style="cyan",
            )
        )


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------

class Phase2_Features:
    """Interview the user to derive and specify all features, then write 01_functional.md."""

    name = "phase-2-features"

    def __init__(self, state: InterviewState) -> None:
        self._state = state
        self._devos_dir = Path.cwd() / ".devos"
        self._agent = PlanningAgent()
        self._gen = SpecGenerator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> InterviewState:
        console.print(
            Panel(
                "[bold cyan]Phase 2 — Features[/bold cyan]\n"
                "We'll enumerate and fully specify every feature.",
                title="[bold]DevOS[/bold]",
                border_style="cyan",
            )
        )

        # Read product spec from disk (fresh — no Phase 1 conversation)
        product_spec_path = Path.cwd() / "spec" / "00_product.md"
        if not product_spec_path.exists():
            raise FileNotFoundError(
                "spec/00_product.md not found. Run Phase 1 first."
            )
        product_spec = product_spec_path.read_text(encoding="utf-8")

        already_confirmed = list(self._state.features)

        # Derive the initial feature list from the product spec
        console.print()
        with Live(
            Text("  Deriving feature list from spec...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            raw_features = self._agent.derive_features(product_spec)

        if already_confirmed:
            # Resume mid-loop: skip list confirmation, continue from last confirmed
            confirmed_list = raw_features
            confirmed_ids = {f.id for f in already_confirmed}
            console.print(
                Panel(
                    f"[yellow]Resuming Phase 2.[/yellow] "
                    f"{len(already_confirmed)} feature(s) already confirmed.\n"
                    f"Continuing from where we left off.",
                    border_style="yellow",
                )
            )
        else:
            # Fresh start: show list and get user confirmation / edits
            confirmed_list = self._edit_feature_list(raw_features)
            confirmed_ids: set[str] = set()

        # ---- Interview each feature ----------------------------------------
        total = len(confirmed_list)
        for idx, feature_draft in enumerate(confirmed_list, 1):
            if feature_draft["id"] in confirmed_ids:
                console.print(
                    f"[dim]  [{idx}/{total}] {feature_draft['id']}: "
                    f"{feature_draft['name']} — already confirmed, skipping.[/dim]"
                )
                continue

            console.print(
                f"\n[bold]Feature {idx} of {total}[/bold]"
            )
            feature = self._interview_feature(product_spec, feature_draft)
            self._state.features.append(feature)
            _persist_state(self._state, self._devos_dir)
            confirmed_ids.add(feature.id)

        # ---- Write 01_functional.md ----------------------------------------
        output_dir = Path.cwd()
        with Live(
            Text("  Writing spec/01_functional.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            spec_path = self._gen.write_functional(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{spec_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        self._state.current_phase = 2
        console.print("\n[bold green]Phase 2 complete.[/bold green]\n")
        return self._state

    # ------------------------------------------------------------------
    # Feature list confirmation
    # ------------------------------------------------------------------

    def _edit_feature_list(self, features: list[dict]) -> list[dict]:
        """Show the derived list and let the user confirm, add, or remove features."""
        while True:
            self._print_feature_list(features)

            try:
                confirmed = Confirm.ask(
                    "\n[bold]Confirm this feature list?[/bold]", default=True
                )
            except EOFError:
                return features  # non-interactive: accept as-is

            if confirmed:
                return features

            console.print(
                "\nEdit commands:\n"
                "  [bold]add <name>[/bold]      — add a new feature\n"
                "  [bold]remove <N>[/bold]      — remove feature number N\n"
            )
            try:
                cmd = Prompt.ask("[bold]Command[/bold]").strip()
            except EOFError:
                return features

            low = cmd.lower()
            if low.startswith("add "):
                name = cmd[4:].strip()
                if not name:
                    console.print("[red]Feature name cannot be empty.[/red]")
                    continue
                if len(features) >= _MAX_FEATURES:
                    self._offer_deferral(name)
                else:
                    new_id = f"F-{len(features) + 1:03d}"
                    features.append(
                        {
                            "id": new_id,
                            "name": name,
                            "description": "User-specified feature.",
                        }
                    )
                    console.print(f"[green]Added: {new_id}: {name}[/green]")

            elif low.startswith("remove "):
                try:
                    num = int(cmd[7:].strip())
                    if 1 <= num <= len(features):
                        removed = features.pop(num - 1)
                        # Re-number remaining
                        for i, feat in enumerate(features):
                            feat["id"] = f"F-{i + 1:03d}"
                        console.print(
                            f"[yellow]Removed: {removed['name']}[/yellow]"
                        )
                    else:
                        console.print(
                            f"[red]Invalid number. Choose 1–{len(features)}.[/red]"
                        )
                except ValueError:
                    console.print("[red]Usage: remove <number>[/red]")
            else:
                console.print(
                    "[red]Unknown command. Use 'add <name>' or 'remove <N>'.[/red]"
                )

    def _print_feature_list(self, features: list[dict]) -> None:
        lines = []
        for feat in features:
            lines.append(f"  [bold]{feat['id']}[/bold]: {feat['name']}")
            if feat.get("description"):
                lines.append(f"       [dim]{feat['description']}[/dim]")
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Derived feature list[/bold cyan]",
                border_style="cyan",
            )
        )

    def _offer_deferral(self, feature_name: str) -> None:
        console.print(
            Panel(
                f"[yellow]Feature cap reached (8 maximum).[/yellow]\n\n"
                f"[bold]{feature_name}[/bold] cannot be added to this session.\n\n"
                "Options:\n"
                "  [bold]1[/bold] — Defer to a future version (recommended)\n"
                "  [bold]2[/bold] — Remove an existing feature first, then add\n"
                "  [bold]3[/bold] — Keep the current list as-is",
                title="[bold yellow]Feature limit reached (8 max)[/bold yellow]",
                border_style="yellow",
            )
        )
        try:
            choice = Prompt.ask("Choose", choices=["1", "2", "3"], default="1")
        except EOFError:
            choice = "1"

        if choice == "1":
            console.print(
                f"[dim]Deferred: '{feature_name}' noted for a future version.[/dim]"
            )
        elif choice == "2":
            console.print(
                "[yellow]Use 'remove <N>' to free a slot, then add again.[/yellow]"
            )
        # choice == "3": do nothing

    # ------------------------------------------------------------------
    # Single-feature interview
    # ------------------------------------------------------------------

    def _interview_feature(
        self, product_spec: str, feature_draft: dict
    ):
        """Run the 4-question template for one feature; returns confirmed Feature."""
        feature_id = feature_draft["id"]
        feature_name = feature_draft["name"]

        while True:  # retry loop if user rejects the built block
            console.print(
                Panel(
                    f"Let's define [bold]{feature_id}: {feature_name}[/bold]",
                    title="[bold]Feature Interview[/bold]",
                    border_style="blue",
                )
            )

            try:
                q1 = Prompt.ask(
                    f"\n[bold][1/4][/bold] How does "
                    f"[bold]{feature_name.lower()}[/bold] enter the system?\n"
                    "      [dim](what triggers it / what's the input channel)[/dim]"
                )
                q2 = Prompt.ask(
                    f"\n[bold][2/4][/bold] What's the minimum "
                    f"[bold]{feature_name.lower()}[/bold] needs to contain?\n"
                    "      [dim](required fields, validation rules)[/dim]"
                )
                q3 = Prompt.ask(
                    f"\n[bold][3/4][/bold] What happens immediately after "
                    f"[bold]{feature_name.lower()}[/bold]?\n"
                    "      [dim](side effects, events emitted, notifications)[/dim]"
                )
                q4 = Prompt.ask(
                    "\n[bold][4/4][/bold] Who can do this?\n"
                    "      [dim](authenticated users only? specific roles? public?)[/dim]"
                )
            except EOFError:
                # Non-interactive default answers
                q1 = f"{feature_name} is submitted via a web form or API request."
                q2 = f"Minimum required fields specific to {feature_name}."
                q3 = "State is persisted and the UI reflects the change."
                q4 = "Authenticated users only."

            answers = {
                "input_channel": q1,
                "data_shape": q2,
                "side_effects": q3,
                "actor_permissions": q4,
            }

            console.print()
            with Live(
                Text("  Building feature specification...", style="dim"),
                console=console,
                refresh_per_second=10,
                transient=True,
            ):
                feature = self._agent.build_feature(
                    product_spec, feature_id, feature_name, answers
                )

            self._print_feature_block(feature)

            try:
                ok = Confirm.ask(
                    f"\n[bold]Confirm {feature_id}: {feature_name}?[/bold]",
                    default=True,
                )
            except EOFError:
                ok = True

            if ok:
                return feature

            console.print(
                "[yellow]Let's redo this feature with different answers.[/yellow]\n"
            )

    def _print_feature_block(self, feature) -> None:
        lines = [
            f"[bold]Trigger:[/bold] {feature.trigger}",
            "",
            "[bold]Preconditions:[/bold]",
        ]
        for pre in feature.preconditions:
            lines.append(f"  - {pre}")
        lines += ["", "[bold]Behavior:[/bold]"]
        for step in feature.behavior:
            lines.append(f"  {step}")
        lines += ["", "[bold]Edge cases:[/bold]"]
        for ec in feature.edge_cases:
            lines.append(f"  - {ec}")
        lines += ["", "[bold]Out of scope:[/bold]"]
        for oos in feature.out_of_scope:
            lines.append(f"  - {oos}")

        console.print(
            Panel(
                "\n".join(lines),
                title=f"[bold cyan]{feature.id}: {feature.name}[/bold cyan]",
                border_style="cyan",
            )
        )


class Phase3_DataAPI:
    """Derive data model + API contract; write spec/02_data_model.md and spec/03_api_contract.md."""

    name = "phase-3-data-api"

    def __init__(self, state: InterviewState) -> None:
        self._state = state
        self._devos_dir = Path.cwd() / ".devos"
        self._agent = PlanningAgent()
        self._gen = SpecGenerator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> InterviewState:
        console.print(
            Panel(
                "[bold cyan]Phase 3 — Data + API[/bold cyan]\n"
                "We'll derive the data model and API contract from the spec.",
                title="[bold]DevOS[/bold]",
                border_style="cyan",
            )
        )

        # ── Fresh context from disk — never from Phase 2 memory ───────────
        product_spec_path = Path.cwd() / "spec" / "00_product.md"
        functional_spec_path = Path.cwd() / "spec" / "01_functional.md"

        if not product_spec_path.exists():
            raise FileNotFoundError("spec/00_product.md not found. Run Phase 1 first.")
        if not functional_spec_path.exists():
            raise FileNotFoundError(
                "spec/01_functional.md not found. Run Phase 2 first."
            )

        product_spec = product_spec_path.read_text(encoding="utf-8")
        functional_spec = functional_spec_path.read_text(encoding="utf-8")

        # ── Step 1: Derive tables + gap questions ──────────────────────────
        console.print()
        with Live(
            Text("  Deriving data model from spec...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            tables, gap_questions = self._agent.derive_data_model(
                product_spec, functional_spec
            )

        # ── Step 2: Ask 0–4 gap questions (only what can't be derived) ─────
        if gap_questions:
            answers = self._agent.ask_schema_questions(gap_questions)
            # Annotate state for audit trail (answers stored on state.constraints)
            for q, a in zip(gap_questions, answers):
                self._state.constraints.append(f"Schema decision — Q: {q} A: {a}")
            _persist_state(self._state, self._devos_dir)

        # ── Step 3: Show table list for confirmation ───────────────────────
        tables = self._confirm_table_list(tables)
        self._state.tables = tables
        _persist_state(self._state, self._devos_dir)

        # ── Step 4: Derive API contract from confirmed tables ──────────────
        console.print()
        with Live(
            Text("  Deriving API contract from spec + tables...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            endpoints = self._agent.derive_api_contract(
                product_spec, functional_spec, tables
            )

        # ── Step 5: Show endpoint list for confirmation ────────────────────
        endpoints = self._confirm_endpoint_list(endpoints)
        self._state.endpoints = endpoints
        _persist_state(self._state, self._devos_dir)

        # ── Step 6: Write both spec files ──────────────────────────────────
        output_dir = Path.cwd()

        with Live(
            Text("  Writing spec/02_data_model.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            dm_path = self._gen.write_data_model(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{dm_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        with Live(
            Text("  Writing spec/03_api_contract.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            api_path = self._gen.write_api_contract(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{api_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        self._state.current_phase = 3
        console.print("\n[bold green]Phase 3 complete.[/bold green]\n")
        return self._state

    # ------------------------------------------------------------------
    # Table list confirmation
    # ------------------------------------------------------------------

    def _confirm_table_list(self, tables: list) -> list:
        """Show table names and let user confirm before proceeding."""
        self._print_table_list(tables)

        try:
            confirmed = Confirm.ask(
                "\n[bold]Confirm this table list?[/bold]", default=True
            )
        except EOFError:
            return tables

        if confirmed:
            return tables

        console.print(
            Panel(
                "[yellow]Tables are derived from the spec. To adjust the schema,\n"
                "edit spec/01_functional.md and re-run Phase 3, or accept and\n"
                "edit spec/02_data_model.md directly after it is written.[/yellow]",
                border_style="yellow",
            )
        )
        return tables

    def _print_table_list(self, tables: list) -> None:
        lines = []
        for table in tables:
            lines.append(f"  [bold]{table.name}[/bold]")
            lines.append(f"    [dim]{table.purpose}[/dim]")
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Derived tables[/bold cyan]",
                border_style="cyan",
            )
        )

    # ------------------------------------------------------------------
    # Endpoint list confirmation
    # ------------------------------------------------------------------

    def _confirm_endpoint_list(self, endpoints: list) -> list:
        """Show method + path for each endpoint and let user confirm."""
        self._print_endpoint_list(endpoints)

        try:
            confirmed = Confirm.ask(
                "\n[bold]Confirm this endpoint list?[/bold]", default=True
            )
        except EOFError:
            return endpoints

        if confirmed:
            return endpoints

        console.print(
            Panel(
                "[yellow]Endpoints are derived from the features in the spec.\n"
                "To adjust, edit spec/01_functional.md and re-run, or accept and\n"
                "edit spec/03_api_contract.md directly after it is written.[/yellow]",
                border_style="yellow",
            )
        )
        return endpoints

    def _print_endpoint_list(self, endpoints: list) -> None:
        lines = []
        for ep in endpoints:
            auth_note = "" if ep.auth_required else " [dim][public][/dim]"
            lines.append(
                f"  [bold]{ep.method}[/bold] {ep.path}{auth_note}"
                f"  [dim]-> {ep.feature_id}[/dim]"
            )
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Derived endpoints[/bold cyan]",
                border_style="cyan",
            )
        )


class Phase4_Architecture:
    """Derive module architecture and constraints; write spec/04_components.md and .devos/constraints.md."""

    name = "phase-4-architecture"

    def __init__(self, state: InterviewState) -> None:
        self._state = state
        self._devos_dir = Path.cwd() / ".devos"
        self._agent = PlanningAgent()
        self._gen = SpecGenerator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> InterviewState:
        console.print(
            Panel(
                "[bold cyan]Phase 4 — Architecture[/bold cyan]\n"
                "We'll define module boundaries and write the constraints file.",
                title="[bold]DevOS[/bold]",
                border_style="cyan",
            )
        )

        # ── Fresh context from disk — never from Phase 3 memory ───────────
        product_spec_path = Path.cwd() / "spec" / "00_product.md"
        functional_spec_path = Path.cwd() / "spec" / "01_functional.md"

        if not product_spec_path.exists():
            raise FileNotFoundError("spec/00_product.md not found. Run Phase 1 first.")
        if not functional_spec_path.exists():
            raise FileNotFoundError(
                "spec/01_functional.md not found. Run Phase 2 first."
            )

        product_spec = product_spec_path.read_text(encoding="utf-8")
        functional_spec = functional_spec_path.read_text(encoding="utf-8")

        # ── Step 1: Ask exactly 2 architecture questions ──────────────────
        questions = [
            "What tech stack should this use?\n"
            "      [dim](Default: FastAPI + SQLAlchemy async / PostgreSQL / no queue / no frontend.[/dim]\n"
            "      [dim]Press Enter to accept the default.)[/dim]",
            "Any hard constraints to respect?\n"
            "      [dim](e.g., must use JWT auth, must support multi-tenancy, max 256 MB RAM.)[/dim]\n"
            "      [dim]Press Enter if none.[/dim]",
        ]
        answers = self._agent.ask_architecture_questions(questions)
        stack_answer = answers[0]
        constraints_answer = answers[1]
        _persist_state(self._state, self._devos_dir)

        # ── Step 2: Derive components ──────────────────────────────────────
        console.print()
        with Live(
            Text("  Deriving module architecture from spec...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            components, stack = self._agent.derive_components(
                product_spec, functional_spec, stack_answer, constraints_answer
            )

        if stack:
            self._state.stack = stack

        # ── Step 3: Show module ownership table for confirmation ───────────
        components = self._confirm_module_list(components)
        self._state.components = components
        _persist_state(self._state, self._devos_dir)

        # ── Step 4: Derive constraints ─────────────────────────────────────
        console.print()
        with Live(
            Text("  Deriving constraints...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            arch_constraints = self._agent.derive_constraints(
                product_spec, functional_spec, components, stack_answer, constraints_answer
            )

        # ── Step 5: Show constraints summary for confirmation ─────────────
        arch_constraints = self._confirm_constraints(arch_constraints)
        self._state.arch_constraints = arch_constraints
        _persist_state(self._state, self._devos_dir)

        # ── Step 6: Write both files ───────────────────────────────────────
        output_dir = Path.cwd()

        with Live(
            Text("  Writing spec/04_components.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            comp_path = self._gen.write_components(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{comp_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        with Live(
            Text("  Writing .devos/constraints.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            cst_path = self._gen.write_constraints(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{cst_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        self._state.current_phase = 4
        console.print("\n[bold green]Phase 4 complete.[/bold green]\n")
        return self._state

    # ------------------------------------------------------------------
    # Module list confirmation
    # ------------------------------------------------------------------

    def _confirm_module_list(self, components: list) -> list:
        """Show module ownership table and let user confirm before proceeding."""
        self._print_module_table(components)

        try:
            confirmed = Confirm.ask(
                "\n[bold]Confirm this module list?[/bold]", default=True
            )
        except EOFError:
            return components

        if confirmed:
            return components

        console.print(
            Panel(
                "[yellow]Modules are derived from the feature list in the spec.\n"
                "To adjust, edit spec/01_functional.md and re-run, or accept and\n"
                "edit spec/04_components.md directly after it is written.[/yellow]",
                border_style="yellow",
            )
        )
        return components

    def _print_module_table(self, components: list) -> None:
        lines = [
            "  [bold]Module[/bold]        [bold]Features[/bold]   [bold]Owns[/bold]",
            "  " + "-" * 60,
        ]
        for comp in components:
            features_str = ", ".join(comp.features) if comp.features else "—"
            lines.append(
                f"  [bold]{comp.name + '/'}[/bold]".ljust(22)
                + f"  {features_str}".ljust(14)
                + f"  [dim]{comp.owns[:50]}[/dim]"
            )
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Derived modules[/bold cyan]",
                border_style="cyan",
            )
        )

    # ------------------------------------------------------------------
    # Constraints confirmation
    # ------------------------------------------------------------------

    def _confirm_constraints(self, arch_constraints) -> object:
        """Show constraints summary and let user confirm."""
        self._print_constraints_summary(arch_constraints)

        try:
            confirmed = Confirm.ask(
                "\n[bold]Confirm these constraints?[/bold]", default=True
            )
        except EOFError:
            return arch_constraints

        if confirmed:
            return arch_constraints

        console.print(
            Panel(
                "[yellow]Constraints are derived from the spec and stack.\n"
                "Accept and edit .devos/constraints.md directly after it is written.[/yellow]",
                border_style="yellow",
            )
        )
        return arch_constraints

    def _print_constraints_summary(self, ac) -> None:
        lines = []
        lines.append(f"[bold]Hard rules[/bold] ({len(ac.hard_rules)}):")
        for rule in ac.hard_rules:
            lines.append(f"  - {rule}")
        lines.append("")
        lines.append(f"[bold]Naming[/bold] ({len(ac.naming)}):")
        for rule in ac.naming:
            lines.append(f"  - {rule}")
        lines.append("")
        lines.append(f"[bold]Always used[/bold] ({len(ac.always_used)}):")
        for rule in ac.always_used:
            lines.append(f"  - {rule}")
        if ac.non_functional:
            lines.append("")
            lines.append(f"[bold]Non-functional[/bold] ({len(ac.non_functional)}):")
            for rule in ac.non_functional:
                lines.append(f"  - {rule}")
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Constraints summary[/bold cyan]",
                border_style="cyan",
            )
        )


class Phase5_Acceptance:
    """Derive acceptance criteria from spec files; write spec/05_acceptance.md."""

    name = "phase-5-acceptance"

    def __init__(self, state: InterviewState) -> None:
        self._state = state
        self._devos_dir = Path.cwd() / ".devos"
        self._agent = PlanningAgent()
        self._gen = SpecGenerator()

    def run(self) -> InterviewState:
        console.print(
            Panel(
                "[bold cyan]Phase 5 — Acceptance Criteria[/bold cyan]\n"
                "We'll derive verifiable done criteria and test stubs for every feature.",
                title="[bold]DevOS[/bold]",
                border_style="cyan",
            )
        )

        # ── Fresh context from disk — never from Phase 4 memory ───────────
        functional_spec_path = Path.cwd() / "spec" / "01_functional.md"
        api_contract_path = Path.cwd() / "spec" / "03_api_contract.md"

        if not functional_spec_path.exists():
            raise FileNotFoundError(
                "spec/01_functional.md not found. Run Phase 2 first."
            )
        if not api_contract_path.exists():
            raise FileNotFoundError(
                "spec/03_api_contract.md not found. Run Phase 3 first."
            )

        functional_spec = functional_spec_path.read_text(encoding="utf-8")
        api_contract = api_contract_path.read_text(encoding="utf-8")

        # ── Step 1: Ask exactly 1 question ────────────────────────────────
        user_answer = self._agent.ask_acceptance_question(
            "What's your definition of done for this version?"
        )
        _persist_state(self._state, self._devos_dir)

        # ── Step 2: Derive acceptance criteria ────────────────────────────
        console.print()
        with Live(
            Text("  Deriving acceptance criteria from spec...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            criteria = self._agent.derive_acceptance(
                functional_spec, api_contract, user_answer
            )

        # ── Step 3: Show per-feature done criteria for confirmation ───────
        criteria = self._confirm_criteria(criteria)
        self._state.acceptance_criteria = criteria
        _persist_state(self._state, self._devos_dir)

        # ── Step 4: Write spec/05_acceptance.md ───────────────────────────
        output_dir = Path.cwd()
        with Live(
            Text("  Writing spec/05_acceptance.md...", style="dim"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            spec_path = self._gen.write_acceptance(self._state, output_dir)

        console.print(
            Panel(
                f"[green]OK[/green] Written: [bold]{spec_path.relative_to(output_dir)}[/bold]",
                border_style="green",
            )
        )

        self._state.current_phase = 5
        console.print("\n[bold green]Phase 5 complete.[/bold green]\n")
        return self._state

    # ------------------------------------------------------------------
    # Per-feature done criteria confirmation
    # ------------------------------------------------------------------

    def _confirm_criteria(self, criteria: list) -> list:
        """Show done criteria counts per feature and ask for confirmation."""
        self._print_criteria_summary(criteria)

        try:
            confirmed = Confirm.ask(
                "\n[bold]Confirm these acceptance criteria?[/bold]", default=True
            )
        except EOFError:
            return criteria

        if confirmed:
            return criteria

        console.print(
            Panel(
                "[yellow]Criteria are derived from the spec.\n"
                "Accept and edit spec/05_acceptance.md directly after it is written.[/yellow]",
                border_style="yellow",
            )
        )
        return criteria

    def _print_criteria_summary(self, criteria: list) -> None:
        lines = [
            "  [bold]Feature[/bold]".ljust(34)
            + "[bold]Done criteria[/bold]  [bold]Tests[/bold]  [bold]Edge cases[/bold]",
            "  " + "-" * 62,
        ]
        for ac in criteria:
            fid_name = f"{ac.feature_id}: {ac.feature_name}"
            lines.append(
                f"  {fid_name[:32].ljust(32)}"
                f"  {len(ac.done_criteria):>5}"
                f"       {len(ac.pytest_stubs):>3}"
                f"       {len(ac.edge_case_coverage):>5}"
            )
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]Acceptance criteria summary[/bold cyan]",
                border_style="cyan",
            )
        )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class InterviewStateMachine:
    """Orchestrates all 5 phases."""

    _phases = [
        Phase1_Capture,
        Phase2_Features,
        Phase3_DataAPI,
        Phase4_Architecture,
        Phase5_Acceptance,
    ]

    def __init__(self) -> None:
        self._devos_dir = Path.cwd() / ".devos"

    def run(self, initial_idea: str) -> InterviewState:
        self._devos_dir.mkdir(parents=True, exist_ok=True)

        # Resume from saved state if available
        state = self._load_state() or InterviewState(idea=initial_idea)
        start_phase = state.current_phase

        for phase_cls in self._phases[start_phase:]:
            phase = phase_cls(state)
            try:
                state = phase.run()
            except Exception as exc:
                self._save_state(state)
                console.print(
                    Panel(
                        f"[red]Error in {phase.name}:[/red] {exc}\n\n"
                        "State saved. Re-run [bold]devos new[/bold] to resume.",
                        border_style="red",
                        title="[bold red]Error[/bold red]",
                    )
                )
                sys.exit(1)

            self._save_state(state)

            # Post-phase commits
            if phase.name == "phase-2-features":
                self._commit_spec(
                    [Path.cwd() / "spec" / "01_functional.md"],
                    "spec(phase-2): functional spec draft",
                )
            elif phase.name == "phase-3-data-api":
                self._commit_spec(
                    [
                        Path.cwd() / "spec" / "02_data_model.md",
                        Path.cwd() / "spec" / "03_api_contract.md",
                    ],
                    "spec(phase-3): data model + API contract",
                )
            elif phase.name == "phase-4-architecture":
                self._commit_spec(
                    [
                        Path.cwd() / "spec" / "04_components.md",
                        Path.cwd() / ".devos" / "constraints.md",
                    ],
                    "spec(phase-4): architecture + constraints",
                )
            elif phase.name == "phase-5-acceptance":
                self._commit_spec(
                    [Path.cwd() / "spec" / "05_acceptance.md"],
                    "spec(phase-5): acceptance criteria",
                )
                self._print_completion_banner()

        return state

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self, state: InterviewState) -> None:
        _persist_state(state, self._devos_dir)

    def _load_state(self) -> InterviewState | None:
        from devos.planning.spec_generator import (
            Feature, Table, Endpoint, Component, ArchConstraints, TechStack,
            AcceptanceCriteria, PytestStub, EdgeCaseMapping,
        )

        path = self._devos_dir / "interview_state.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))

            # Restore confirmed features
            features: list[Feature] = []
            for f_data in data.get("features", []):
                try:
                    features.append(Feature(**f_data))
                except Exception:
                    pass

            # Restore confirmed tables
            tables: list[Table] = []
            for t_data in data.get("tables", []):
                try:
                    t_data.setdefault("out_of_scope", [])
                    tables.append(Table(**t_data))
                except Exception:
                    pass

            # Restore confirmed endpoints
            endpoints: list[Endpoint] = []
            for e_data in data.get("endpoints", []):
                try:
                    e_data.setdefault("purpose", "")
                    e_data.setdefault("out_of_scope", [])
                    endpoints.append(Endpoint(**e_data))
                except Exception:
                    pass

            # Restore components
            components: list[Component] = []
            for c_data in data.get("components", []):
                try:
                    c_data.setdefault("internal_structure", [])
                    c_data.setdefault("interfaces_exposed", [])
                    c_data.setdefault("may_import", [])
                    c_data.setdefault("must_never_import", [])
                    c_data.setdefault("features", [])
                    components.append(Component(**c_data))
                except Exception:
                    pass

            # Restore arch_constraints
            arch_constraints: ArchConstraints | None = None
            ac_data = data.get("arch_constraints")
            if ac_data:
                try:
                    ac_data.setdefault("non_functional", [])
                    arch_constraints = ArchConstraints(**ac_data)
                except Exception:
                    pass

            # Restore stack
            stack: TechStack | None = None
            stack_data = data.get("stack")
            if stack_data:
                try:
                    stack_data.setdefault("extras", [])
                    stack_data.setdefault("frontend", None)
                    stack_data.setdefault("queue", None)
                    stack = TechStack(**stack_data)
                except Exception:
                    pass

            # Restore acceptance_criteria
            acceptance_criteria: list[AcceptanceCriteria] = []
            for ac_item in data.get("acceptance_criteria", []):
                try:
                    stubs = [
                        PytestStub(**s) for s in ac_item.get("pytest_stubs", [])
                    ]
                    edge_cov = [
                        EdgeCaseMapping(**m)
                        for m in ac_item.get("edge_case_coverage", [])
                    ]
                    acceptance_criteria.append(
                        AcceptanceCriteria(
                            feature_id=ac_item["feature_id"],
                            feature_name=ac_item["feature_name"],
                            done_criteria=ac_item.get("done_criteria", []),
                            pytest_stubs=stubs,
                            edge_case_coverage=edge_cov,
                        )
                    )
                except Exception:
                    pass

            loaded_phase = data.get("current_phase", 0)

            # Sanity checks: phase counter is only valid when output was produced.
            if loaded_phase >= 5 and not acceptance_criteria:
                loaded_phase = 4  # Phase 5 was never actually completed
            if loaded_phase >= 4 and not components:
                loaded_phase = 3  # Phase 4 was never actually completed
            if loaded_phase >= 3 and not tables:
                loaded_phase = 2  # Phase 3 was never actually completed
            if loaded_phase >= 2 and not features:
                loaded_phase = 1  # Phase 2 was never actually completed
            if loaded_phase >= 1 and not data.get("vision"):
                loaded_phase = 0  # Phase 1 was never actually completed

            return InterviewState(
                idea=data["idea"],
                vision=data.get("vision", ""),
                problem=data.get("problem", ""),
                users=data.get("users", []),
                in_scope=data.get("in_scope", []),
                out_of_scope=data.get("out_of_scope", []),
                non_negotiables=data.get("non_negotiables", []),
                success_criteria=data.get("success_criteria", ""),
                constraints=data.get("constraints", []),
                current_phase=loaded_phase,
                features=features,
                tables=tables,
                endpoints=endpoints,
                stack=stack,
                components=components,
                arch_constraints=arch_constraints,
                acceptance_criteria=acceptance_criteria,
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Completion banner
    # ------------------------------------------------------------------

    def _print_completion_banner(self) -> None:
        spec_files = [
            Path.cwd() / "spec" / "00_product.md",
            Path.cwd() / "spec" / "01_functional.md",
            Path.cwd() / "spec" / "02_data_model.md",
            Path.cwd() / "spec" / "03_api_contract.md",
            Path.cwd() / "spec" / "04_components.md",
            Path.cwd() / "spec" / "05_acceptance.md",
        ]
        lines = ["[bold green]All 5 phases complete. Full spec written to spec/[/bold green]", ""]
        for p in spec_files:
            if p.exists():
                line_count = len(p.read_text(encoding="utf-8").splitlines())
                rel = str(p.relative_to(Path.cwd())).replace("\\", "/")
                lines.append(f"  [green]OK[/green]  [bold]{rel}[/bold]  [dim]({line_count} lines)[/dim]")
            else:
                rel = str(p.relative_to(Path.cwd())).replace("\\", "/")
                lines.append(f"  [red]--[/red]  {rel}  [dim](missing)[/dim]")
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold green]DevOS — Spec Complete[/bold green]",
                border_style="green",
            )
        )

    # ------------------------------------------------------------------
    # Git integration
    # ------------------------------------------------------------------

    def _commit_spec(self, spec_files: list[Path], message: str) -> None:
        try:
            import git

            repo = git.Repo(Path.cwd())
            for f in spec_files:
                if f.exists():
                    rel = str(f.relative_to(Path.cwd())).replace("\\", "/")
                    repo.index.add([rel])
            repo.index.commit(message)
            console.print(f"[dim]git commit: {message}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Warning: git commit skipped — {exc}[/yellow]")
