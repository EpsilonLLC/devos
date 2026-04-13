"""DevOS CLI entry point."""

from __future__ import annotations

import hashlib
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
from rich.table import Table as RichTable

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


@cli.command()
def build() -> None:
    """Parse the spec and generate .devos/task_graph.json."""
    from devos.decomposition.spec_parser import SpecParser, SpecValidationError
    from devos.decomposition.dependency_graph import DependencyGraph
    from devos.decomposition.task_graph import TaskGraph

    spec_dir = Path.cwd() / "spec"
    devos_dir = Path.cwd() / ".devos"
    output_path = devos_dir / "task_graph.json"

    if not spec_dir.exists():
        console.print(
            Panel(
                "[red]No spec/ directory found in the current working directory.[/red]\n"
                "Run [bold]devos new[/bold] to generate the spec first.",
                title="[bold red]Build failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    console.print(
        Panel(
            "[bold cyan]DevOS Build[/bold cyan] — Decomposition Layer",
            border_style="cyan",
        )
    )

    # ── Step 1: Parse spec ────────────────────────────────────────────
    parser = SpecParser()
    try:
        with console.status("[cyan]Parsing spec files...[/cyan]"):
            parsed_spec = parser.parse(spec_dir)
    except SpecValidationError as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="[bold red]Spec validation failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)
    except FileNotFoundError as exc:
        console.print(
            Panel(
                f"[red]Missing spec file:[/red] {exc.filename}",
                title="[bold red]Build failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    console.print(
        f"[green]✓[/green] Spec parsed — "
        f"[bold]{len(parsed_spec.features)}[/bold] features  "
        f"[bold]{len(parsed_spec.tables)}[/bold] tables  "
        f"[bold]{len(parsed_spec.endpoints)}[/bold] endpoints  "
        f"[bold]{len(parsed_spec.components)}[/bold] components"
    )

    # ── Step 2: Build dependency graph ────────────────────────────────
    with console.status("[cyan]Building dependency graph...[/cyan]"):
        dep_graph = DependencyGraph.build(parsed_spec)

    console.print(
        f"[green]✓[/green] Dependency graph — "
        f"[bold]{len(dep_graph.tasks)}[/bold] tasks"
    )

    # ── Step 3: Compute spec hash ─────────────────────────────────────
    spec_hash = _compute_spec_hash(spec_dir)

    # ── Step 4: Assign waves and write graph ──────────────────────────
    with console.status("[cyan]Assigning execution waves...[/cyan]"):
        task_graph = TaskGraph.build(dep_graph)
        task_graph.write(output_path, spec_hash)

    # ── Step 5: Print summary table ───────────────────────────────────
    table = RichTable(
        title="Task Graph",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Wave", style="bold cyan", justify="center", width=6)
    table.add_column("Task ID", style="bold", width=8)
    table.add_column("Name", width=24)
    table.add_column("Component", width=12)
    table.add_column("Depends on", width=20)

    for wave in task_graph.waves:
        for task in sorted(wave.tasks, key=lambda t: t.id):
            deps = ", ".join(task.depends_on) if task.depends_on else "[dim]—[/dim]"
            table.add_row(
                str(wave.wave),
                task.id,
                task.name,
                task.component,
                deps,
            )

    console.print(table)
    console.print(
        Panel(
            f"[bold green].devos/task_graph.json written[/bold green]\n"
            f"[bold]{len(dep_graph.tasks)}[/bold] tasks across "
            f"[bold]{len(task_graph.waves)}[/bold] waves\n"
            f"[dim]spec_hash: {spec_hash[:16]}...[/dim]",
            border_style="green",
        )
    )


@cli.command()
@click.option("--task", "task_id", default=None, help="Task ID to run (e.g. T-001).")
def run(task_id: str | None) -> None:
    """Run a single task through the full execution pipeline.

    Assembles context, creates an isolated git worktree, launches Claude Code
    non-interactively, monitors to completion, and distills the output.

    If --task is omitted the first task in Wave 0 is selected (lexicographic).
    Fails with a clear error if task_graph.json is absent or the spec has
    changed since the last `devos build`.
    """
    import json

    import anthropic

    from devos.agents.claude_code import ClaudeCodeAdapter
    from devos.context.assembler import ContextAssembler
    from devos.context.distiller import MemoryDistiller
    from devos.context.injector import ConstraintInjector, ConstraintsMissingError
    from devos.decomposition.dependency_graph import Task
    from devos.execution.monitor import SessionMonitor
    from devos.execution.reaction import ReactionEngine
    from devos.execution.spawner import AgentSpawner, SpawnError
    from devos.execution.worktree import WorktreeManager

    repo_root = Path.cwd()
    spec_dir = repo_root / "spec"
    devos_dir = repo_root / ".devos"
    task_graph_path = devos_dir / "task_graph.json"

    # ── Guard: task_graph.json must exist ────────────────────────────────────
    if not task_graph_path.exists():
        console.print(
            Panel(
                "[red]No task graph found.[/red]\n"
                "Run [bold]devos build[/bold] first.",
                title="[bold red]Run failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    graph_data = json.loads(task_graph_path.read_text(encoding="utf-8"))

    # ── Guard: spec hash must match ──────────────────────────────────────────
    try:
        current_hash = _compute_spec_hash(spec_dir)
    except FileNotFoundError:
        console.print(
            Panel(
                "[red]One or more spec files are missing.[/red]\n"
                "Run [bold]devos build[/bold] to regenerate the task graph.",
                title="[bold red]Run failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    stored_hash = graph_data.get("spec_hash", "")
    if current_hash != stored_hash:
        console.print(
            Panel(
                "[red]Spec has changed since last build.[/red]\n"
                "Run [bold]devos build[/bold] again.",
                title="[bold red]Stale task graph[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    # ── Select task ──────────────────────────────────────────────────────────
    all_tasks_by_id: dict[str, dict] = {
        t["id"]: t
        for wave in graph_data["waves"]
        for t in wave["tasks"]
    }

    if task_id:
        if task_id not in all_tasks_by_id:
            console.print(
                Panel(
                    f"[red]Task {task_id!r} not found in task_graph.json.[/red]",
                    title="[bold red]Run failed[/bold red]",
                    border_style="red",
                )
            )
            sys.exit(1)
        task_dict = all_tasks_by_id[task_id]
    else:
        # Pick first task in Wave 0, lexicographic by task ID
        wave_0 = next(
            (w for w in graph_data["waves"] if w["wave"] == 0), None
        )
        if not wave_0 or not wave_0["tasks"]:
            console.print(
                Panel(
                    "[red]No Wave 0 tasks found in task_graph.json.[/red]",
                    title="[bold red]Run failed[/bold red]",
                    border_style="red",
                )
            )
            sys.exit(1)
        task_dict = min(wave_0["tasks"], key=lambda t: t["id"])

    task = Task(**task_dict)

    # ── Resolve prior summaries from dependencies ────────────────────────────
    prior_summaries: list[Path] = []
    for dep_id in task.depends_on:
        summary_path = devos_dir / "summaries" / f"{dep_id}.md"
        if summary_path.exists():
            prior_summaries.append(summary_path)
        else:
            console.print(
                f"[yellow]Warning:[/yellow] summary for dependency {dep_id} "
                f"not found at {summary_path} — proceeding without it."
            )

    # ── Wire the execution layer ─────────────────────────────────────────────
    try:
        injector = ConstraintInjector(devos_dir / "constraints.md")
    except ConstraintsMissingError as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]\n"
                "Ensure [bold].devos/constraints.md[/bold] exists and is non-empty.",
                title="[bold red]Run failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    assembler = ContextAssembler(repo_root, spec_dir, devos_dir, injector)
    adapter = ClaudeCodeAdapter()
    worktree_manager = WorktreeManager(repo_root)
    spawner = AgentSpawner(repo_root, devos_dir, assembler, worktree_manager, adapter)
    anthropic_client = anthropic.Anthropic()
    distiller = MemoryDistiller(anthropic_client)
    monitor = SessionMonitor(devos_dir, adapter)
    reaction = ReactionEngine(devos_dir, distiller, worktree_manager, spawner, monitor)

    # ── Run ──────────────────────────────────────────────────────────────────
    console.print(
        Panel(
            f"[bold cyan]DevOS Run[/bold cyan] — {task.id}: {task.name}\n"
            f"[dim]Agent:[/dim] ClaudeCodeAdapter  "
            f"[dim]Prior summaries:[/dim] {len(prior_summaries)}",
            border_style="cyan",
        )
    )

    try:
        with console.status(f"[cyan]Spawning agent for {task.id}...[/cyan]"):
            session = spawner.spawn(task, prior_summaries)
    except SpawnError as exc:
        console.print(
            Panel(
                f"[red]{exc}[/red]",
                title="[bold red]Spawn failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)

    console.print(
        f"[green]✓[/green] Agent spawned — session [bold]{session.session_id}[/bold]"
    )

    session = monitor.watch(session)

    # ── React ────────────────────────────────────────────────────────────────
    if session.status == "complete":
        try:
            summary = reaction.on_complete(session, task)
        except Exception as exc:
            console.print(
                Panel(
                    f"[red]{exc}[/red]",
                    title="[bold red]Post-completion reaction failed[/bold red]",
                    border_style="red",
                )
            )
            sys.exit(1)
        console.print(
            Panel(
                f"[bold green]{task.id} complete.[/bold green]\n\n"
                f"[dim]Summary:[/dim]\n{summary.rendered_markdown}",
                title=f"[bold green]Done — {task.name}[/bold green]",
                border_style="green",
            )
        )

    elif session.status == "stalled":
        reaction.on_stall(session, task)
        sys.exit(2)

    elif session.status == "failed":
        reaction.on_failure(session, task)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_spec_hash(spec_dir: Path) -> str:
    """SHA-256 of all 6 spec files concatenated in deterministic order."""
    spec_files = [
        spec_dir / "00_product.md",
        spec_dir / "01_functional.md",
        spec_dir / "02_data_model.md",
        spec_dir / "03_api_contract.md",
        spec_dir / "04_components.md",
        spec_dir / "05_acceptance.md",
    ]
    hasher = hashlib.sha256()
    for f in spec_files:
        hasher.update(f.read_bytes())
    return hasher.hexdigest()

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
