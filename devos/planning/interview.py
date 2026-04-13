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

console = Console(highlight=False)

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
    name = "phase-3-data-api"

    def __init__(self, state: InterviewState) -> None:
        self._state = state

    def run(self) -> InterviewState:
        console.print("[dim]Phase 3 (Data + API) — not yet implemented[/dim]")
        self._state.current_phase = 3
        return self._state


class Phase4_Architecture:
    name = "phase-4-architecture"

    def __init__(self, state: InterviewState) -> None:
        self._state = state

    def run(self) -> InterviewState:
        console.print("[dim]Phase 4 (Architecture) — not yet implemented[/dim]")
        self._state.current_phase = 4
        return self._state


class Phase5_Acceptance:
    name = "phase-5-acceptance"

    def __init__(self, state: InterviewState) -> None:
        self._state = state

    def run(self) -> InterviewState:
        console.print("[dim]Phase 5 (Acceptance) — not yet implemented[/dim]")
        self._state.current_phase = 5
        return self._state


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

            # Post-phase hook: commit spec after Phase 2
            if phase.name == "phase-2-features":
                self._commit_spec(
                    [Path.cwd() / "spec" / "01_functional.md"],
                    "spec(phase-2): functional spec draft",
                )

            # Stop once Phase 2 is complete (Phase 3+ not yet implemented)
            if state.current_phase >= 2:
                break

        return state

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self, state: InterviewState) -> None:
        _persist_state(state, self._devos_dir)

    def _load_state(self) -> InterviewState | None:
        from devos.planning.spec_generator import Feature

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
                    pass  # Skip any malformed feature entries

            loaded_phase = data.get("current_phase", 0)

            # Sanity check: phase counter may have been inflated by old stubs.
            # A phase is only truly done when it produced real output.
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
            )
        except Exception:
            return None

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
