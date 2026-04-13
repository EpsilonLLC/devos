# DevOS

## What this project is
A personal CLI tool that takes a plain-language idea through a structured
interview, produces a 6-file spec, and executes parallel AI agents with
surgical context management to build complete applications.

## Tech stack
- Python 3.12
- Click (CLI framework)
- Rich (terminal UI)
- Anthropic SDK (LLM calls)
- Pydantic v2 (data models)
- gitpython (worktree management)

## Architecture
Four layers: Planning → Decomposition → Execution → Synthesis
Full design in: DEVOS_HANDOFF.md

## Hard rules
- Phase isolation: each interview phase runs in a fresh LLM context
- Never store conversation history in InterviewState
- constraints.md is always injected first, never summarized away
- Each phase reads spec files from disk — not from memory
- Commit spec files to git after each phase completes

## Module map
| Module | Owns | Must not |
|--------|------|----------|
| planning/ | Interview, spec generation, planning agent | Touch execution |
| decomposition/ | Spec parsing, dependency graph, task graph | Touch agents |
| context/ | Context assembly, distillation, injection | Touch git directly |
| execution/ | Worktrees, agent spawning, monitoring | Touch spec files |
| synthesis/ | Diff collection, validation, merging | Touch agent processes |

## Current build phase
Phase 1: CLI interview + planning agent → 00_product.md

## Key files
- DEVOS_HANDOFF.md — full architecture and design decisions
- devos/planning/interview.py — interview state machine
- devos/planning/planning_agent.py — LLM calls per phase
- devos/planning/spec_generator.py — writes spec files to disk
- devos/cli.py — entry point
