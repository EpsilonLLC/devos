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
# Stub phases (2–5) — not yet implemented
# ---------------------------------------------------------------------------

class Phase2_Features:
    name = "phase-2-features"

    def __init__(self, state: InterviewState) -> None:
        self._state = state

    def run(self) -> InterviewState:
        console.print("[dim]Phase 2 (Features) — not yet implemented[/dim]")
        self._state.current_phase = 2
        return self._state


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

            # Only Phase 1 is implemented — stop after it for now
            if state.current_phase == 1:
                break

        return state

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self, state: InterviewState) -> None:
        path = self._devos_dir / "interview_state.json"
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

    def _load_state(self) -> InterviewState | None:
        path = self._devos_dir / "interview_state.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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
                current_phase=data.get("current_phase", 0),
            )
        except Exception:
            return None
