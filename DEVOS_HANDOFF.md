# DevOS — Full Architecture Handoff
> Personal Software Development OS  
> Status: Architecture complete, Phase 1 ready to build  
> Last updated: April 2026

---

## What This Is

DevOS is a personal CLI tool that takes a plain-language idea and produces a
working, tested application using parallel AI agents. It is agent-agnostic,
git-native, and designed from the ground up around one core principle:

> **Agents never accumulate context. They receive exactly what they need,
> nothing more, and their output is distilled before any downstream agent
> sees it.**

The system has four layers:

```
Idea → [Planning Layer] → Spec → [Decomposition Layer] → Task Graph
     → [Execution Layer] → Agent Outputs → [Synthesis Layer] → Codebase
```

---

## Design Decisions (Locked)

These were decided deliberately. Do not relitigate them.

| Decision | Choice | Reason |
|---|---|---|
| Agent support | Fully agent-agnostic via adapter interface | Future-proof |
| User interface | CLI only | No UI overhead, ships fast, works anywhere |
| Spec storage | Git repo flat files | Versioned, diffable, agents read natively |
| Context strategy | Surgical per-task packages | Prevents degradation by design |
| LLM per phase | Fresh context each phase | No accumulated conversation rot |
| Feature cap | 8 features max per session | 15-25 task graph, optimal for parallel |
| Spec zones | Zone 1 locked / Zone 2 guided | Prevents agent drift on critical paths |

---

## Project Structure

```
devos/
├── devos/                        # Core Python package
│   ├── __init__.py
│   ├── cli.py                    # Entry point — `devos` command
│   │
│   ├── planning/
│   │   ├── __init__.py
│   │   ├── interview.py          # Phase 1-5 state machine
│   │   ├── planning_agent.py     # LLM calls per phase
│   │   └── spec_generator.py     # Writes spec files to disk
│   │
│   ├── decomposition/
│   │   ├── __init__.py
│   │   ├── spec_parser.py        # Reads spec/, extracts entities
│   │   ├── dependency_graph.py   # Builds DAG, detects conflicts
│   │   └── task_graph.py         # Writes task_graph.json
│   │
│   ├── context/
│   │   ├── __init__.py
│   │   ├── assembler.py          # Builds surgical pkg per task
│   │   ├── distiller.py          # Compresses output → summary
│   │   └── injector.py           # Injects constraints.md always
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── worktree.py           # gitpython worktree management
│   │   ├── spawner.py            # Launches agent processes
│   │   ├── monitor.py            # Detects stall / completion
│   │   └── reaction.py           # CI fail / test fail routing
│   │
│   ├── agents/                   # Agent adapters (plugin slots)
│   │   ├── __init__.py
│   │   ├── base.py               # Abstract AgentAdapter interface
│   │   ├── claude_code.py        # Claude Code adapter
│   │   ├── codex.py              # Codex adapter
│   │   └── aider.py              # Aider adapter
│   │
│   └── synthesis/
│       ├── __init__.py
│       ├── collector.py          # Gathers all worktree diffs
│       ├── validator.py          # Checks diffs vs acceptance
│       └── merger.py             # Human gate + merge
│
├── spec/                         # Lives inside target project repo
│   ├── 00_product.md
│   ├── 01_functional.md
│   ├── 02_data_model.md
│   ├── 03_api_contract.md
│   ├── 04_components.md
│   └── 05_acceptance.md
│
├── .devos/                       # Runtime state (gitignored)
│   ├── task_graph.json
│   ├── interview_state.json
│   ├── sessions/
│   ├── summaries/
│   └── constraints.md
│
├── AGENTS.md                     # Agent-readable project context
├── pyproject.toml
└── README.md
```

---

## pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "devos"
version = "0.1.0"
description = "A personal software development OS — idea to application using parallel AI agents"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.40.0",
    "click>=8.1.0",
    "rich>=13.0.0",
    "pydantic>=2.0.0",
    "gitpython>=3.1.0",
]

[project.scripts]
devos = "devos.cli:cli"

[tool.setuptools.packages.find]
where = ["."]
include = ["devos*"]
```

---

## CLI Entry Point Design

```
devos new "idea string"   → Starts planning interview
devos plan                → Reviews / edits existing spec
devos plan --amend        → Amends locked spec (reruns affected AC)
devos build               → Decomposition + execution
devos review              → Opens synthesis review
devos ship                → Final merge + commit
devos status              → Shows active agent sessions
```

---

## Core Data Structures

### InterviewState

```python
from dataclasses import dataclass, field
from typing import Optional
from pydantic import BaseModel

class Feature(BaseModel):
    id: str                      # F-001, F-002, etc.
    name: str
    status: str                  # included | excluded | deferred
    trigger: str
    preconditions: list[str]
    behavior: list[str]          # Numbered steps
    edge_cases: list[str]
    out_of_scope: list[str]

class Table(BaseModel):
    name: str
    purpose: str
    columns: list[dict]          # name, type, nullable, default, constraint
    indexes: list[str]
    relationships: list[str]
    invariants: list[str]

class Endpoint(BaseModel):
    method: str                  # GET POST PATCH DELETE
    path: str                    # /api/v1/...
    auth_required: bool
    request_body: Optional[dict]
    response_200: dict
    error_responses: list[dict]  # scenario, http_code, code
    side_effects: list[str]
    feature_id: str              # F-001 etc — cross reference

class TechStack(BaseModel):
    backend: str                 # e.g. "FastAPI + SQLAlchemy async"
    frontend: Optional[str]      # e.g. "Next.js 14" or None
    database: str                # e.g. "PostgreSQL"
    queue: Optional[str]         # e.g. "Redis + Celery" or None
    extras: list[str]            # any other constraints

@dataclass
class InterviewState:
    idea: str
    vision: str = ""
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
    # NOTE: conversation history is NEVER stored here
    # Each phase reads spec files from disk, not from memory
```

### AgentAdapter (abstract base)

```python
from abc import ABC, abstractmethod
from pathlib import Path

class AgentAdapter(ABC):
    """
    Every coding agent plugs in by implementing this interface.
    The rest of the system never knows which agent is running.
    """

    @abstractmethod
    def build_launch_command(self, context_path: Path) -> list[str]:
        """Return the shell command to launch this agent with context."""

    @abstractmethod
    def deliver_context(self, session_id: str, context: dict) -> None:
        """Deliver the surgical context package to a running agent."""

    @abstractmethod
    def is_complete(self, session_id: str) -> bool:
        """Return True when the agent has finished its task."""

    @abstractmethod
    def is_stalled(self, session_id: str) -> bool:
        """Return True when degradation or stall is detected."""

    @abstractmethod
    def get_output(self, session_id: str) -> str:
        """Return the agent's completed output for distillation."""
```

### TaskNode (dependency graph)

```python
class TaskNode(BaseModel):
    id: str                          # T-001, T-002, etc.
    name: str
    feature_id: str                  # Which feature this implements
    module: str                      # Which module it writes to
    write_targets: list[str]         # Files this task writes
    read_targets: list[str]          # Files this task reads
    dependencies: list[str]         # Task IDs that must complete first
    spec_sections: list[str]        # Spec files needed (max 3)
    parallelizable: bool            # Can run alongside others
    estimated_exchanges: int        # Expected LLM turns (target <25)
```

---

## The Spec Schema — All 6 Files

### 00_product.md

```markdown
# Product

## Vision
[One sentence — what + for whom]

## Problem
[2-3 sentences — what painful thing does this replace]

## Users
| User | Goal | Constraint |
|------|------|------------|
| [type] | [what they want] | [their limitation] |

## Scope
### In scope
- [explicit list]

### Out of scope
- [explicit list — as important as in-scope]

## Success criteria
[Plain language — how do you know it works]

## Non-negotiables
[Hard constraints that override every other decision]
```

### 01_functional.md

```markdown
# Functional specification

## Feature schema
Every feature uses this exact structure:

---
### F-001: [Name]
**Status:** included | excluded | deferred
**Zone:** locked

**Trigger:** [What causes this to activate]

**Preconditions:**
- [Must be true before this runs]

**Behavior:**
1. [Numbered, unambiguous steps]
2. [Each step implementable without clarification]

**Edge cases:**
- [Every named deviation from happy path]

**Out of scope for this feature:**
- [What this feature explicitly does not do]
---
```

### 02_data_model.md

```markdown
# Data model

## Conventions (locked — agents must follow exactly)
- All tables: snake_case
- All PKs: `id UUID DEFAULT gen_random_uuid()`
- All tables: `created_at TIMESTAMPTZ DEFAULT NOW()`
- All tables: `updated_at TIMESTAMPTZ DEFAULT NOW()`
- Soft deletes: `deleted_at TIMESTAMPTZ NULL`
- Tenant scoping: every table except `tenants` has `tenant_id UUID NOT NULL`

## Tables

---
### `[table_name]`
**Purpose:** [One sentence]

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |

**Indexes:**
- [index definitions]

**Relationships:**
- [FK definitions with ON DELETE behavior]

**Invariants:**
- [Rules enforced in code, not just DB]

**Out of scope:**
- [What this table does NOT store]
---
```

### 03_api_contract.md

```markdown
# API contract

## Conventions (locked)
- Base path: `/api/v1`
- Auth: Bearer token in Authorization header (all unless marked [public])
- All requests: Content-Type: application/json
- Timestamps: ISO 8601 UTC
- IDs: UUID strings

## Error envelope (locked — every error response)
{
  "error": "human_readable_message",
  "code": "MACHINE_READABLE_CODE",
  "detail": {}
}

## Standard error codes
| HTTP | Code | Meaning |
|------|------|---------|
| 400 | VALIDATION_ERROR | Request body invalid |
| 401 | UNAUTHORIZED | Missing or invalid token |
| 403 | FORBIDDEN | Insufficient permission |
| 404 | NOT_FOUND | Resource does not exist |
| 409 | CONFLICT | State conflict |
| 422 | UNPROCESSABLE | Valid syntax, invalid semantics |
| 429 | RATE_LIMITED | Too many requests |
| 500 | INTERNAL_ERROR | Server fault |

---
### `[METHOD] [path]` [public if applicable]
**Purpose:** [One sentence]
**Feature:** [F-00X]

**Request body:**
[JSON schema with field descriptions]

**Response 200:**
[JSON schema]

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|

**Side effects:**
- [What else happens when this endpoint is called]

**Out of scope:**
- [What this endpoint does not do]
---
```

### 04_components.md

```markdown
# Component architecture

## Module ownership (locked — no cross-module direct access)
| Module | Owns | Must not |
|--------|------|----------|
| [name] | [domain] | [violation] |

## Patterns (guided)
- Repository pattern: All DB access through repository classes
- Service layer: Business logic in service classes, never in routes
- Dependency injection: Constructor args, no global state

---
### Module: `[name]/`
**Responsibility:** [One sentence]

**Internal structure (guided):**
[directory tree]

**Interfaces exposed:**
[The ONLY things other modules import from this module]

**May import:**
- [list]

**Must never import:**
- [list]
---
```

### 05_acceptance.md

```markdown
# Acceptance criteria

---
### AC-F001: [Feature name]
**Feature:** F-001
**Status:** required

**Functional checks:**
- [ ] [File exists and contains expected pattern]
- [ ] [Logic present]

**Behavioral tests:**
[pytest test stubs — must pass in CI]

**Edge case coverage required:**
- [ ] [specific edge case]

**Out of scope for acceptance:**
- [What is not tested here]
---
```

---

## constraints.md (always injected — keep short)

```markdown
# Constraints
# Injected at TOP of every agent context. Never summarized away.

## Hard rules — violation fails validation
- All DB queries include tenant_id filter
- No raw SQL outside db/repositories/
- All endpoints return standard error envelope
- No secrets in code — environment variables only
- All async functions use async def

## Naming
- Files: snake_case
- Classes: PascalCase
- Functions/variables: snake_case
- Constants: UPPER_SNAKE_CASE

## Always used
- Repository pattern for all DB access
- Pydantic v2 for all request/response models
- Explicit error handling — no bare except:
```

---

## Interview State Machine — 5 Phases

### Phase structure

```python
class InterviewStateMachine:
    phases = [
        Phase1_Capture,
        Phase2_Features,
        Phase3_DataAPI,
        Phase4_Architecture,
        Phase5_Acceptance,
    ]

    def run(self, initial_idea: str) -> InterviewState:
        state = InterviewState(idea=initial_idea)

        for PhaseClass in self.phases:
            phase = PhaseClass(state)
            # Each phase: fresh LLM context
            # Reads spec files written by previous phases from disk
            # Does NOT receive previous phase conversation history
            state = phase.run()
            self.write_spec_files(state)
            self.commit_draft(phase.name)

        return self.gate1_confirmation(state)
```

### Phase 1 — Capture (writes 00_product.md)

**Flow:**
1. User inputs idea (min 10 chars, max 500)
2. Agent reflects understanding back — one sentence
3. User confirms or corrects (max one clarifying question per turn)
4. Agent asks exactly 3 questions:
   - "What does a user do the moment they log in?" (core workflow)
   - "What's the one thing this version does NOT do?" (scope boundary)
   - "Who is NOT the user of this product?" (anti-persona)
5. Agent writes 00_product.md
6. Prints "Phase 1 complete"

**LLM system prompt:**
```
You are a product specification agent in Phase 1: Capture.

Your job: extract a clear product vision from a short user description
and write 00_product.md.

Rules:
- Reflect understanding back BEFORE asking anything
- Ask maximum ONE clarifying question per turn during reflection
- After confirmation, ask EXACTLY these three questions in order:
  1. What does a user do the moment they log in?
  2. What is the one thing this version does NOT do?
  3. Who is NOT the user of this product?
- Do NOT ask about tech stack, database, or implementation
- Do NOT ask about features in detail (that is Phase 2)
- Do NOT suggest features the user did not mention
- Do NOT ask more than 3 questions total

Output: Write 00_product.md following the schema exactly.
Then output the marker: <<<PHASE_1_COMPLETE>>>

Schema to follow: [00_product.md schema injected here]
```

### Phase 2 — Features (writes 01_functional.md)

**Flow:**
1. Agent reads 00_product.md from disk
2. Derives initial feature list (never more than 8)
3. Shows list, asks user to confirm / add / remove
4. For each feature, asks exactly 4 questions:
   - How does it enter the system? (input channel)
   - What is the minimum data it needs? (data shape)
   - What happens immediately after? (side effects)
   - Who can do this? (actor + permissions)
5. Shows feature block for confirmation before moving on
6. Hard cap: if user tries to add 9th feature, offers deferral

**Feature interview template (4 questions — works for any feature):**
```
Let's define [F-00X]: [name]

1. How does [thing] enter the system?
   (what triggers it / what's the input channel)

2. What's the minimum [thing] needs to contain?
   (required fields, validation rules)

3. What happens immediately after [thing] is created/triggered?
   (side effects, events emitted, notifications)

4. Who can [do this action]?
   (authenticated users only? specific roles? public?)
```

**LLM system prompt:**
```
You are a product specification agent in Phase 2: Features.

Input: 00_product.md (read from disk, provided below)
Your job: enumerate and fully specify all features.

Rules:
- Derive the initial feature list from 00_product.md — do not invent
- Show the full list FIRST, get confirmation before going deep
- Maximum 8 features. If user requests a 9th, offer deferral options
- For each feature, ask EXACTLY the 4 template questions — no more
- Show the written feature block for user confirmation before next feature
- Do NOT move to next feature until current one is confirmed
- Do NOT ask about database schema or API design (that is Phase 3)
- Feature IDs are F-001 through F-008

Output: Write 01_functional.md with all confirmed features.
Then output: <<<PHASE_2_COMPLETE>>>

Input spec file:
[00_product.md content injected here]

Schema to follow:
[01_functional.md schema injected here]
```

### Phase 3 — Data + API (writes 02 + 03)

**Flow:**
1. Agent reads 00_product.md + 01_functional.md from disk
2. Derives full data model automatically
3. Asks only schema-affecting questions (2-4 max)
4. Shows table list for confirmation (not full schema)
5. Derives API endpoints from features automatically
6. Asks only one structural question about endpoints
7. Shows endpoint list for confirmation
8. Writes both files

**LLM system prompt:**
```
You are a product specification agent in Phase 3: Data + API.

Input: 00_product.md and 01_functional.md (provided below)
Your job: derive the data model and API contract.

Rules:
- DERIVE tables from features — ask only when derivation is impossible
- Maximum 4 clarifying questions total across data model and API
- Every question must be about a specific schema decision, not open-ended
- Show entity list (not full schema) for confirmation
- Every feature must map to at least one endpoint
- Apply conventions from schema exactly (UUID PKs, timestamps, soft deletes)
- Every endpoint maps to a feature via feature_id field

Output: Write 02_data_model.md and 03_api_contract.md.
Then output: <<<PHASE_3_COMPLETE>>>

Input spec files:
[00_product.md content]
[01_functional.md content]

Schemas to follow:
[02 and 03 schemas injected here]
```

### Phase 4 — Architecture (writes 04 + constraints.md)

**Flow:**
1. Agent reads 01_functional.md from disk
2. Asks exactly 2 questions:
   - Tech stack (with sensible defaults offered)
   - Hard constraints to respect
3. Derives module structure from feature list automatically
4. Writes 04_components.md and .devos/constraints.md

**LLM system prompt:**
```
You are a product specification agent in Phase 4: Architecture.

Input: 01_functional.md (provided below)
Your job: define module boundaries and constraints.

Rules:
- Ask EXACTLY 2 questions: tech stack + hard constraints
- Offer sensible defaults for tech stack (user can just press Enter)
- Derive module structure from features — one module per domain
- constraints.md must be SHORT — max 20 lines, declarative only
- Module ownership table must be machine-parseable (used by decomposer)
- Every module must declare what it may and must never import

Output: Write 04_components.md and .devos/constraints.md.
Then output: <<<PHASE_4_COMPLETE>>>

Input spec file:
[01_functional.md content]

Schemas to follow:
[04 schema and constraints.md format injected here]
```

### Phase 5 — Acceptance (writes 05_acceptance.md)

**Flow:**
1. Agent reads 01_functional.md + 03_api_contract.md from disk
2. Derives all acceptance criteria automatically
3. Shows summary (counts per feature, not full criteria)
4. Asks if any feature needs stricter criteria
5. Writes 05_acceptance.md

**LLM system prompt:**
```
You are a product specification agent in Phase 5: Acceptance.

Input: 01_functional.md and 03_api_contract.md (provided below)
Your job: write acceptance criteria for every feature.

Rules:
- DERIVE criteria from spec — do not ask open questions
- Every feature gets: functional checks (diff-readable) + test stubs
- Functional checks must be checkboxes readable by a validator agent
- Test stubs must be valid pytest syntax
- Ask ONE question only: "Any feature needing stricter criteria?"
- Security-adjacent features (auth, permissions, payments) get extra checks

Output: Write 05_acceptance.md.
Then output: <<<PHASE_5_COMPLETE>>>

Input spec files:
[01_functional.md content]
[03_api_contract.md content]

Schema to follow:
[05_acceptance.md schema injected here]
```

---

## Context Management System

### The surgical context package (per task)

```
.devos/sessions/{task_id}/context/
  task.md              # This task only — trigger, behavior, acceptance
  spec_sections/       # Only relevant spec files (max 3)
  relevant_files/      # Only files this task reads or writes (max 5)
  interfaces.md        # Contracts with adjacent modules (not their impl)
  constraints.md       # Always injected — from .devos/constraints.md
  prior_summaries/     # Distilled output from dependency tasks
```

### Token budget (hard limits)

```python
MAX_SPEC_SECTIONS_PER_TASK = 3
MAX_FILES_PER_TASK = 5
MAX_EXCHANGES_PER_SESSION = 25
MAX_CONTEXT_TOKENS = 40_000     # Well below degradation threshold
DISTILLED_SUMMARY_TARGET = 200  # tokens per completed task
```

### Memory distiller output format

```markdown
# Task summary: [task_id] — [task_name]
**Completed:** [timestamp]
**Status:** done | partial | failed

## Decisions made
- [Decision with spec reference, e.g. "Used JWT 15min expiry (spec §3.2)"]

## Files modified
- [path/to/file.py]

## Interfaces produced
- [function signature or API endpoint that other tasks depend on]

## Deviations from spec
- [None] or [specific deviation with justification]

## Tests written
- [test_function_name: what it tests]

## Outstanding
- [None] or [what was left incomplete and why]
```

### Constraint injection rule

```python
# injector.py
# constraints.md is ALWAYS injected at position 0 in every context.
# It is NEVER summarized by the distiller.
# It is NEVER omitted even if the task seems unrelated.
# Reason: agents reweight recent tokens. Early injection = max attention.
```

---

## Dependency Graph Rules

### Parallelism rule (the hard one)

Two tasks can run in parallel if and only if:
- They have zero shared write targets (no shared files)
- They have zero shared tables in write mode
- Neither depends on an interface the other produces

### Conflict detection

```python
def can_parallelize(task_a: TaskNode, task_b: TaskNode) -> bool:
    # Shared write targets = conflict
    if set(task_a.write_targets) & set(task_b.write_targets):
        return False
    # If B reads something A writes, B depends on A
    if set(task_b.read_targets) & set(task_a.write_targets):
        return False
    # If A reads something B writes, A depends on B
    if set(task_a.read_targets) & set(task_b.write_targets):
        return False
    return True
```

### Execution order

```
Wave 0: Tasks with no dependencies       → all run in parallel
Wave 1: Tasks depending only on Wave 0  → run in parallel after Wave 0
Wave 2: Tasks depending on Wave 0 or 1  → run in parallel after Wave 1
...
```

---

## Build Sequence (5 Phases)

| Phase | What to build | Deliverable |
|---|---|---|
| **1** | Spec schema + CLI interview + planning agent | `devos new` works end-to-end |
| **2** | Context assembler + memory distiller | Context surgery system complete |
| **3** | Decomposition layer | `task_graph.json` generated from spec |
| **4** | Execution layer — single agent first | One agent runs a task correctly |
| **5** | Parallel execution + synthesis layer | Full system end-to-end |

**Start with Phase 1. Do not skip ahead.**

Phase 2 (context system) comes before decomposition deliberately — you need
the context package format locked before writing the assembler or distiller.

---

## Token Cost Reference

| Phase | Tokens | Cost (Sonnet 4.6) |
|---|---|---|
| Planning session (all 5 phases) | ~30,000 | ~$0.23 |
| Per coding task (execution) | ~19,500 | ~$0.10 |
| Full 8-feature app (planning + execution) | ~420,000 | ~$2.25 |

Planning is <10% of total cost. Execution is where tokens go.
Surgical context management keeps per-task cost flat regardless of codebase size.

---

## AGENTS.md Template

```markdown
# DevOS

## What this project is
[One sentence from 00_product.md vision]

## Tech stack
[From 04_components.md]

## Spec location
All product requirements, data model, API contracts, and acceptance
criteria live in spec/. Read relevant sections before writing any code.

## Hard rules (also in .devos/constraints.md)
- [copy non-negotiables here]

## Module map
[copy module ownership table from 04_components.md]

## Current task
See: .devos/sessions/{SESSION_ID}/context/task.md

## Do not
- Write code not described in your task.md
- Modify files outside your write_targets list
- Import from modules outside your allowed imports list
- Ask clarifying questions — if spec is ambiguous, implement
  the most conservative interpretation and note the deviation
```

---

## What to Build First (Claude Code Instructions)

Start here. Build in this exact order:

### Step 1 — spec_generator.py
Function that takes an `InterviewState` and writes `00_product.md`
to the correct path in the correct schema format.
Test: call it with a mock state, confirm file is written correctly.

### Step 2 — planning_agent.py
Class with a `run_phase1(idea: str) -> dict` method.
Makes one Anthropic API call with the Phase 1 system prompt.
Parses the response into structured fields for InterviewState.
Test: call with "a todo app for developers" — confirm structured output.

### Step 3 — interview.py
Phase1_Capture class. CLI loop using Rich for display.
Handles: input validation, reflection turn, 3 questions, confirmation.
Calls planning_agent.run_phase1(), then spec_generator.write_product().
Test: run interactively, confirm 00_product.md is written.

### Step 4 — cli.py
Click command group. `devos new [idea]` invokes Phase1_Capture.
If idea not provided as arg, prompt for it.
Test: `devos new "a task manager"` runs full Phase 1.

### Step 5 — Phase 2 (after Phase 1 is solid)
Do not start Phase 2 until Phase 1 is tested and working.

---

## Key Implementation Notes

**Rich for all CLI output** — use panels, spinners, and confirm prompts.
Never use plain `print()` in the interview flow.

**Phase isolation is non-negotiable** — each phase class reads spec files
from disk, never from the previous phase's LLM conversation. This is the
core of the context degradation prevention strategy.

**Commit after each phase** — use gitpython to commit spec files after
each phase completes. Message format: `spec(phase-N): [phase name] draft`

**Interview state serialization** — serialize InterviewState to
`.devos/interview_state.json` after each phase. If the process is
interrupted, resume from last completed phase, not from scratch.

**Error handling** — if any LLM call fails, save current state and exit
gracefully with a resume instruction. Never lose completed phase work.

**The <<<PHASE_N_COMPLETE>>> marker** — this is how the state machine
knows a phase is done. Parse for this marker in every LLM response.
If it's absent after 3 retries, raise an error and save state.
