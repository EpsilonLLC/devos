# Claude Code Opening Prompt
# Paste this entire block as your first message in Claude Code

I'm building DevOS — a personal software development OS that takes a
plain-language idea through a CLI interview, produces a structured 6-file
spec, and will eventually execute parallel AI agents with surgical context
management to prevent degradation.

The full architecture is designed and documented in DEVOS_HANDOFF.md at the
root of this project. Read that file completely before writing any code.

Today's goal: build Phase 1 end-to-end so that `devos new "idea"` runs a
CLI interview and writes spec/00_product.md to disk.

Build in this order:

1. devos/planning/spec_generator.py
   - SpecGenerator class
   - write_product(state: InterviewState, output_dir: Path) -> Path
   - Writes 00_product.md following the schema in the handoff doc
   - Creates spec/ directory if it doesn't exist

2. devos/planning/planning_agent.py
   - PlanningAgent class
   - run_phase1(idea: str) -> dict
   - Makes one Anthropic API call with the Phase 1 system prompt
   - System prompt is defined in the handoff doc
   - Returns structured dict that maps to InterviewState fields
   - Handles the <<<PHASE_1_COMPLETE>>> marker
   - Model: claude-sonnet-4-6

3. devos/planning/interview.py
   - InterviewState dataclass (from handoff doc)
   - Phase1_Capture class with run() -> InterviewState
   - Uses Rich for all output (panels, spinners, Confirm prompts)
   - Input validation: min 10 chars, max 500 chars, max 3 retries
   - Calls PlanningAgent then SpecGenerator
   - Serializes state to .devos/interview_state.json
   - InterviewStateMachine class that orchestrates all phases
     (stub Phase 2-5 for now — just Phase 1 working)

4. devos/cli.py
   - Click group: cli
   - Command: new (takes optional idea argument, prompts if not given)
   - Creates .devos/ directory if not present
   - Invokes InterviewStateMachine
   - Clean error handling with Rich error panels

5. Test it: run `devos new "a task management app for developers"` and
   confirm spec/00_product.md is written with correct structure.

Rules while building:
- Read DEVOS_HANDOFF.md before starting — it has all schemas and prompts
- Each file must be complete and working before moving to the next
- Use python-dotenv or os.environ for ANTHROPIC_API_KEY
- No placeholder comments — every function must be implemented
- After each file is complete, show me the output of a quick test
