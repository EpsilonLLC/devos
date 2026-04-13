"""DevOS CLI entry point."""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminals that default to cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console(highlight=False)

_MIN_IDEA_LEN = 10
_MAX_IDEA_LEN = 500
_MAX_RETRIES = 3


@click.group()
def cli() -> None:
    """DevOS — idea to application using parallel AI agents."""


@cli.command()
@click.argument("idea", required=False)
def new(idea: str | None) -> None:
    """Start a new project from an idea.

    IDEA  Plain-language description of what you want to build.
          If omitted, you will be prompted.
    """
    # Ensure .devos/ directory exists
    devos_dir = Path.cwd() / ".devos"
    devos_dir.mkdir(parents=True, exist_ok=True)

    # Prompt for idea if not provided as argument
    if not idea:
        idea = _prompt_for_idea()

    # Validate idea
    idea = _validate_idea(idea)

    # Run the interview state machine
    from devos.planning.interview import InterviewStateMachine

    machine = InterviewStateMachine()
    try:
        machine.run(idea)
    except KeyboardInterrupt:
        console.print(
            Panel(
                "[yellow]Interrupted. Your progress has been saved.[/yellow]\n"
                "Re-run [bold]devos new[/bold] to resume.",
                border_style="yellow",
            )
        )
        sys.exit(0)
    except Exception as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="[bold red]Unexpected error[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prompt_for_idea() -> str:
    """Interactively prompt the user for an idea with validation."""
    for attempt in range(_MAX_RETRIES):
        raw = Prompt.ask("[bold cyan]What do you want to build?[/bold cyan]")
        stripped = raw.strip()

        if len(stripped) < _MIN_IDEA_LEN:
            console.print(
                f"[yellow]Too short — please describe your idea in at least "
                f"{_MIN_IDEA_LEN} characters.[/yellow]"
            )
            continue

        if len(stripped) > _MAX_IDEA_LEN:
            console.print(
                f"[yellow]Too long — keep it under {_MAX_IDEA_LEN} characters. "
                f"({len(stripped)} chars)[/yellow]"
            )
            continue

        return stripped

    console.print("[red]Too many invalid attempts. Exiting.[/red]")
    sys.exit(1)


def _validate_idea(idea: str) -> str:
    """Validate and return stripped idea, or exit with an error panel."""
    stripped = idea.strip()

    if len(stripped) < _MIN_IDEA_LEN:
        console.print(
            Panel(
                f"[red]Idea too short.[/red] Must be at least {_MIN_IDEA_LEN} characters.\n"
                f"Got: [bold]{repr(stripped)}[/bold]",
                border_style="red",
                title="[bold red]Validation error[/bold red]",
            )
        )
        sys.exit(1)

    if len(stripped) > _MAX_IDEA_LEN:
        console.print(
            Panel(
                f"[red]Idea too long.[/red] Must be under {_MAX_IDEA_LEN} characters.\n"
                f"Got: {len(stripped)} chars.",
                border_style="red",
                title="[bold red]Validation error[/bold red]",
            )
        )
        sys.exit(1)

    return stripped
