"""LLM calls for each interview phase."""

from __future__ import annotations

import json
import os
import re

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

load_dotenv(encoding="utf-8-sig")

_agent_console = Console(highlight=False, force_terminal=True, legacy_windows=False)

# ---------------------------------------------------------------------------
# Phase 5 prompt
# ---------------------------------------------------------------------------

_PHASE5_ACCEPTANCE_SYSTEM = """\
You are a product specification agent in Phase 5: Acceptance Criteria.

Input: spec/01_functional.md, spec/03_api_contract.md, and the user's definition of done.
Your job: write compact acceptance criteria for every feature in the spec.

STRICT OUTPUT LIMITS — stay within these or you will run out of tokens:
- done_criteria: exactly 5 items per feature (3 happy path + 2 error cases)
- pytest_stubs: exactly 4 items per feature — one per major scenario
- edge_case_coverage: one entry per edge case listed in the feature's "Edge cases" section
  (copy the exact edge case text from the spec — do NOT add extra ones)

Quality rules:
- Done criteria must be verifiable statements referencing HTTP codes or field names
  Good: "POST /api/v1/tasks returns 201 with task.id UUID and status 'todo'"
  Bad:  "Task creation works correctly"
- Pytest stub names must be scenario-specific
  Good: test_create_task_rejects_empty_title
  Bad:  test_task_creation_works
- Every edge case from 01_functional.md MUST map to a test — do NOT skip any
- The user's definition of done must influence done_criteria

Output: a JSON array wrapped in <ACCEPTANCE_JSON>...</ACCEPTANCE_JSON> tags.
One compact object per feature, in feature order (F-001 through F-007):

[
  {
    "feature_id": "F-001",
    "feature_name": "User Authentication",
    "done_criteria": [
      "POST /api/v1/auth/signup with valid email+password returns 200 with {user: {id, email}, token: string}",
      "POST /api/v1/auth/login with correct credentials returns 200 with non-empty token",
      "GET /api/v1/auth/session with valid Bearer token returns 200 with user object",
      "POST /api/v1/auth/signup with duplicate email returns 409 CONFLICT code",
      "Any protected endpoint without Authorization header returns 401 UNAUTHORIZED"
    ],
    "pytest_stubs": [
      {"name": "test_signup_creates_user_and_returns_token", "docstring": "POST /signup with valid unique email and password returns 200 with user.id UUID and token string."},
      {"name": "test_signup_rejects_duplicate_email", "docstring": "POST /signup with already-registered email returns 409 with CONFLICT code."},
      {"name": "test_login_correct_credentials_returns_token", "docstring": "POST /login with correct email and password returns 200 with valid non-empty token."},
      {"name": "test_protected_endpoint_rejects_missing_auth_header", "docstring": "GET on any auth-required endpoint without Authorization header returns 401 UNAUTHORIZED."}
    ],
    "edge_case_coverage": [
      {"edge_case": "Email already registered", "test": "test_signup_rejects_duplicate_email"},
      {"edge_case": "Wrong password at login", "test": "test_login_rejects_wrong_password"},
      {"edge_case": "Non-existent email at login", "test": "test_login_rejects_unknown_email"}
    ]
  }
]

After the closing </ACCEPTANCE_JSON> tag, output: <<<PHASE_5_COMPLETE>>>
"""

# ---------------------------------------------------------------------------
# Phase 4 prompts
# ---------------------------------------------------------------------------

_PHASE4_COMPONENTS_SYSTEM = """\
You are a product specification agent in Phase 4: Architecture — Component Derivation.

Input: 00_product.md, 01_functional.md, and the user's tech stack and constraint answers (all provided below).
Your job: derive the module architecture.

Rules:
- One module per domain area (e.g., auth, tasks, projects, notifications, core)
- Every module MUST list the F-00X feature IDs it owns (from 01_functional.md)
- Every module must declare may_import and must_never_import to prevent circular dependencies
- internal_structure must be a realistic directory listing with inline comments
- interfaces_exposed: ONLY the public API of the module — specific class or function names other modules import
- must_not: a concrete violation that would break the architecture
- Also parse and return the tech stack from the user's stack answer
- If the user's stack answer is empty or vague, default to:
  backend="FastAPI + SQLAlchemy async", database="PostgreSQL", frontend=null, queue=null, extras=[]

Output: A JSON object wrapped in <COMPONENTS_JSON>...</COMPONENTS_JSON> tags:
{
  "stack": {
    "backend": "FastAPI + SQLAlchemy async",
    "frontend": null,
    "database": "PostgreSQL",
    "queue": null,
    "extras": []
  },
  "components": [
    {
      "name": "auth",
      "responsibility": "One sentence describing the module's domain.",
      "owns": "User identity, authentication tokens, sessions",
      "must_not": "Access task or project tables directly",
      "features": ["F-001"],
      "internal_structure": [
        "auth/",
        "  routes.py        # FastAPI router",
        "  service.py       # AuthService class",
        "  repository.py    # DB queries only",
        "  schemas.py       # Pydantic request/response models"
      ],
      "interfaces_exposed": [
        "AuthService",
        "get_current_user"
      ],
      "may_import": ["db/", "core/config", "core/exceptions"],
      "must_never_import": ["tasks/", "projects/", "notifications/"]
    }
  ]
}

Then output the marker: <<<PHASE_4_COMPONENTS_DERIVED>>>
"""

_PHASE4_CONSTRAINTS_SYSTEM = """\
You are a product specification agent in Phase 4: Architecture — Constraints Derivation.

Input: 00_product.md, 01_functional.md, the derived component architecture, and the user's answers (all provided below).
Your job: write the content for .devos/constraints.md.

CRITICAL: constraints.md is injected at position 0 in EVERY future agent context window.
It MUST be SHORT and declarative. Max 5 items per section. Every line must be a specific, actionable rule.
Do NOT write descriptions, justifications, or comments — only the rules themselves.

Derive everything from the spec and stack. Do not invent constraints not supported by the input.

Rules to follow:
- hard_rules: constraints that fail code validation if violated (include tenant scoping, error envelope, no raw SQL outside repositories, etc.)
- naming: file/class/function/constant conventions
- always_used: architectural patterns that every module must apply
- non_functional: performance, security, or reliability requirements derived from the spec (only include if explicitly implied)

Output: A JSON object wrapped in <CONSTRAINTS_JSON>...</CONSTRAINTS_JSON> tags:
{
  "hard_rules": [
    "All DB queries include tenant_id filter",
    "No raw SQL outside {module}/repository.py",
    "All endpoints return standard error envelope {error, code, detail}",
    "No secrets in code — environment variables only",
    "All async functions use async def"
  ],
  "naming": [
    "Files: snake_case",
    "Classes: PascalCase",
    "Functions/variables: snake_case",
    "Constants: UPPER_SNAKE_CASE"
  ],
  "always_used": [
    "Repository pattern: all DB access through repository classes",
    "Service layer: business logic in service classes, never in routes",
    "Pydantic v2 for all request/response models",
    "Explicit error handling — no bare except:"
  ],
  "non_functional": [
    "All endpoints require authentication unless explicitly marked public"
  ]
}

Then output the marker: <<<PHASE_4_CONSTRAINTS_DERIVED>>>
"""

# ---------------------------------------------------------------------------
# Phase 3 prompts
# ---------------------------------------------------------------------------

_PHASE3_DATA_SYSTEM = """\
You are a product specification agent in Phase 3: Data Model Derivation.

Input: 00_product.md and 01_functional.md (provided below).
Your job: derive the complete relational data model for this application.

Conventions to apply automatically to EVERY table (no exceptions):
- Table names: snake_case
- Every table has: id UUID NOT NULL DEFAULT gen_random_uuid() — PRIMARY KEY
- Every table has: created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- Every table has: updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- Every table has: deleted_at TIMESTAMPTZ NULL  (soft deletes)
- Every table EXCEPT "tenants": tenant_id UUID NOT NULL REFERENCES tenants(id)

Derivation rules:
- Derive tables directly from the features in 01_functional.md
- Every noun that is stored/queried is a table
- Many-to-many relationships get a join table
- Do NOT ask about things you can derive — only identify genuine ambiguities
- Identify 0–4 gap questions about schema decisions that CANNOT be derived
  (e.g., "Should tags be a separate table or a JSON array on tasks?")
  Gap questions must be specific schema decisions, never open-ended.

Output: A single JSON object wrapped in <DATA_MODEL_JSON>...</DATA_MODEL_JSON> tags:
{
  "tables": [
    {
      "name": "table_name",
      "purpose": "One sentence describing what this table stores.",
      "columns": [
        {
          "name": "id",
          "type": "UUID",
          "nullable": false,
          "default": "gen_random_uuid()",
          "constraint": "PK"
        }
      ],
      "indexes": ["CREATE INDEX idx_name ON table(col)"],
      "relationships": ["tenant_id REFERENCES tenants(id) ON DELETE CASCADE"],
      "invariants": ["Rule enforced in application code, not just DB"],
      "out_of_scope": ["What this table does NOT store"]
    }
  ],
  "gap_questions": [
    "Should tags be a separate table with a join to tasks, or a text[] array column on tasks?"
  ]
}

Include ALL standard columns (id, tenant_id, created_at, updated_at, deleted_at) in every table's
columns array. Then output the marker: <<<PHASE_3_DATA_DERIVED>>>
"""

_PHASE3_API_SYSTEM = """\
You are a product specification agent in Phase 3: API Contract Derivation.

Input: 00_product.md, 01_functional.md, and confirmed data model tables (all provided below).
Your job: derive the complete REST API contract.

Conventions to apply automatically:
- Base path: /api/v1
- Auth: Bearer token in Authorization header (all endpoints unless marked auth_required: false)
- All responses use Content-Type: application/json
- Timestamps: ISO 8601 UTC strings
- IDs: UUID strings
- Every error response uses the standard error envelope:
  {"error": "message", "code": "MACHINE_CODE", "detail": {}}

Derivation rules:
- EVERY feature from 01_functional.md must map to at least one endpoint
- Use RESTful conventions: GET (read), POST (create), PATCH (update), DELETE (delete)
- feature_id must reference the exact F-00X ID from 01_functional.md
- request_body: null for GET/DELETE; a dict with field→description for POST/PATCH
- response_200: a dict describing the JSON structure returned on success
- error_responses: list of {scenario, http_code, code} for all named failure cases
- side_effects: what else happens when this endpoint is called (DB writes, events, etc.)
- out_of_scope: what this endpoint explicitly does NOT do

Output: A single JSON object wrapped in <API_CONTRACT_JSON>...</API_CONTRACT_JSON> tags:
{
  "endpoints": [
    {
      "method": "POST",
      "path": "/api/v1/auth/signup",
      "auth_required": false,
      "purpose": "Create a new user account and return a session token.",
      "request_body": {
        "email": "string (required, valid email format)",
        "password": "string (required, min 8 characters)"
      },
      "response_200": {
        "user": {"id": "uuid", "email": "string"},
        "token": "string (JWT, expires in 24h)"
      },
      "error_responses": [
        {"scenario": "Email already registered", "http_code": 409, "code": "CONFLICT"},
        {"scenario": "Invalid email format or short password", "http_code": 400, "code": "VALIDATION_ERROR"}
      ],
      "side_effects": [
        "Creates a new record in the users table",
        "Generates a session token stored server-side"
      ],
      "feature_id": "F-001",
      "out_of_scope": ["Does not send a verification email"]
    }
  ]
}

Then output the marker: <<<PHASE_3_API_DERIVED>>>
"""

# ---------------------------------------------------------------------------
# Phase 2 prompts
# ---------------------------------------------------------------------------

_PHASE2_DERIVE_SYSTEM = """\
You are a product specification agent in Phase 2: Features — Derivation step.

Input: 00_product.md (provided below)
Your job: enumerate the features that need to be built based on the product spec.

Rules:
- Derive features ONLY from what is explicitly stated or clearly implied in the
  "In scope" section of the product spec
- Do NOT invent features not mentioned in the spec
- Maximum 8 features total — if in-scope items imply more, combine related ones
- Each feature must be a single, distinct, implementable capability
- Use clear action-noun names (e.g., "Task Creation", "User Authentication")

Output: A JSON array inside <FEATURE_LIST>...</FEATURE_LIST> tags.
Each item: {"id": "F-001", "name": "Action Noun", "description": "One sentence."}
IDs run F-001 through F-008 (never higher).

Example:
<FEATURE_LIST>
[
  {"id": "F-001", "name": "User Authentication", "description": "Users can register and log in with credentials."},
  {"id": "F-002", "name": "Task Creation", "description": "Authenticated users can create tasks with required fields."}
]
</FEATURE_LIST>

Then output the marker: <<<PHASE_2_DERIVED>>>
"""

_PHASE2_BUILD_SYSTEM = """\
You are a product specification agent in Phase 2: Features — Feature Build step.

Input: product spec + feature interview answers.
Your job: write a complete, unambiguous Feature specification block.

Rules:
- Use ONLY the information in the product spec and interview answers — do not
  invent details beyond what was answered
- Behavior steps must be numbered, concrete, and each individually implementable
  without any further clarification
- Edge cases must be specific and concrete (no generic "invalid input" catch-alls)
- Out of scope must reference what THIS feature explicitly does NOT do
- Preconditions must be runtime conditions that the system must verify before
  executing the feature — not general truisms

Output: A JSON object inside <FEATURE_JSON>...</FEATURE_JSON> tags:
{
  "id": "F-00X",
  "name": "Feature Name",
  "status": "included",
  "trigger": "What causes this feature to activate (the input channel / event)",
  "preconditions": ["Specific condition that must be true at runtime"],
  "behavior": ["1. First numbered concrete step", "2. Second step"],
  "edge_cases": ["Specific deviation from the happy path"],
  "out_of_scope": ["What this feature explicitly does not do"]
}

Then output the marker: <<<PHASE_2_FEATURE_BUILT>>>
"""

_PHASE1_SYSTEM = """\
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

After gathering all answers, produce a JSON object wrapped in
<PRODUCT_JSON>...</PRODUCT_JSON> tags with this exact shape:

{
  "vision": "One sentence — what + for whom",
  "problem": "2-3 sentences — what painful thing does this replace",
  "users": [
    {"user": "type", "goal": "what they want", "constraint": "their limitation"}
  ],
  "in_scope": ["explicit list of things included in this version"],
  "out_of_scope": ["explicit list — as important as in-scope"],
  "success_criteria": "Plain language — how do you know it works",
  "non_negotiables": ["Hard constraints that override every other decision"]
}

Then output the marker: <<<PHASE_1_COMPLETE>>>

Schema reference for 00_product.md:
# Product
## Vision
[One sentence — what + for whom]
## Problem
[2-3 sentences — what painful thing does this replace]
## Users
| User | Goal | Constraint |
## Scope
### In scope / ### Out of scope
## Success criteria
## Non-negotiables
"""

_PHASE1_USER_TEMPLATE = """\
Here is the product idea:

{idea}

Please reflect your understanding back to me, then ask your 3 questions \
(one at a time or all at once if you feel confident enough), and finally \
produce the PRODUCT_JSON and <<<PHASE_1_COMPLETE>>> marker.

For the purpose of this automated flow, after your reflection you may \
ask all 3 questions in a single message and provide reasonable inferences \
from the idea itself — you do not need to wait for interactive answers. \
Base the users / scope / non-negotiables on the idea as stated.
"""


class PlanningAgent:
    """Makes LLM calls for each planning phase."""

    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Add it to .env or export it."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def run_phase1(self, idea: str) -> dict:
        """
        Run Phase 1: Capture.

        Makes one Anthropic API call with the Phase 1 system prompt.
        Returns a dict with keys matching InterviewState fields:
          vision, problem, users, in_scope, out_of_scope,
          success_criteria, non_negotiables
        """
        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_PHASE1_SYSTEM,
            messages=[
                {"role": "user", "content": _PHASE1_USER_TEMPLATE.format(idea=idea)}
            ],
        )

        response_text = message.content[0].text

        if "<<<PHASE_1_COMPLETE>>>" not in response_text:
            raise ValueError(
                "Phase 1 marker <<<PHASE_1_COMPLETE>>> not found in LLM response.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_phase1_response(response_text)

    def derive_features(self, product_spec: str) -> list[dict]:
        """
        Phase 2 — Derivation step.

        Given the content of 00_product.md, call the LLM to enumerate the
        initial feature list.  Returns a list of dicts with keys:
          id, name, description
        """
        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_PHASE2_DERIVE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"Here is 00_product.md:\n\n{product_spec}",
                }
            ],
        )

        response_text = message.content[0].text

        if "<<<PHASE_2_DERIVED>>>" not in response_text:
            raise ValueError(
                "Phase 2 derivation marker <<<PHASE_2_DERIVED>>> not found.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_feature_list(response_text)

    def build_feature(
        self,
        product_spec: str,
        feature_id: str,
        feature_name: str,
        answers: dict,
    ):
        """
        Phase 2 — Feature Build step.

        Given the product spec, the feature being interviewed, and the user's
        four answers, produce a fully-specified Feature object.

        answers keys: input_channel, data_shape, side_effects, actor_permissions
        """
        from devos.planning.spec_generator import Feature  # avoid circular at module level

        user_content = (
            f"Product spec:\n{product_spec}\n\n"
            f"Feature to specify: {feature_id}: {feature_name}\n\n"
            "Interview answers:\n"
            f"Q1 (How does it enter the system?): {answers['input_channel']}\n"
            f"Q2 (Minimum data it needs?): {answers['data_shape']}\n"
            f"Q3 (What happens immediately after?): {answers['side_effects']}\n"
            f"Q4 (Who can do this?): {answers['actor_permissions']}"
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_PHASE2_BUILD_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        if "<<<PHASE_2_FEATURE_BUILT>>>" not in response_text:
            raise ValueError(
                f"Phase 2 feature-build marker not found for {feature_id}.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_feature(response_text, feature_id, feature_name)

    def derive_data_model(
        self, product_spec: str, functional_spec: str
    ) -> tuple[list, list[str]]:
        """
        Phase 3 — Data model derivation.

        Reads 00_product.md and 01_functional.md content (already loaded from disk
        by the caller — fresh context, no Phase 2 memory).

        Returns (tables, gap_questions) where:
          tables       — list of Table Pydantic objects
          gap_questions — 0-4 strings, schema decisions that cannot be derived
        """
        from devos.planning.spec_generator import Table

        user_content = (
            "Here is 00_product.md:\n\n"
            f"{product_spec}\n\n"
            "Here is 01_functional.md:\n\n"
            f"{functional_spec}\n\n"
            "Derive the complete data model. Apply all conventions automatically. "
            "Include all standard columns (id, tenant_id, created_at, updated_at, "
            "deleted_at) in every table. Identify 0–4 gap questions only for "
            "decisions that cannot be derived."
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=_PHASE3_DATA_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        if "<<<PHASE_3_DATA_DERIVED>>>" not in response_text:
            raise ValueError(
                "Phase 3 data model marker <<<PHASE_3_DATA_DERIVED>>> not found.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_data_model(response_text)

    def derive_api_contract(
        self,
        product_spec: str,
        functional_spec: str,
        tables: list,
    ) -> list:
        """
        Phase 3 — API contract derivation.

        Derives endpoints from spec files + confirmed table schema.
        Every feature from 01_functional.md maps to at least one endpoint.

        Returns list of Endpoint Pydantic objects.
        """
        import json as _json
        from devos.planning.spec_generator import Endpoint

        tables_json = _json.dumps(
            [t.model_dump() for t in tables], indent=2
        )

        user_content = (
            "Here is 00_product.md:\n\n"
            f"{product_spec}\n\n"
            "Here is 01_functional.md:\n\n"
            f"{functional_spec}\n\n"
            "Here are the confirmed data model tables:\n\n"
            f"{tables_json}\n\n"
            "Derive the complete API contract. Every feature must map to at least "
            "one endpoint. Apply all conventions automatically."
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=_PHASE3_API_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        if "<<<PHASE_3_API_DERIVED>>>" not in response_text:
            raise ValueError(
                "Phase 3 API contract marker <<<PHASE_3_API_DERIVED>>> not found.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_api_contract(response_text)

    def ask_acceptance_question(self, question: str) -> str:
        """
        Ask exactly 1 acceptance question via Rich CLI. No LLM call.

        question — the question string to present to the user.
        Returns the user's answer; falls back to a sensible default in
        non-interactive mode (EOFError on stdin).
        """
        _agent_console.print(
            Panel(
                "[bold]One question before we derive acceptance criteria.[/bold]",
                title="[bold cyan]Acceptance Criteria[/bold cyan]",
                border_style="cyan",
            )
        )
        _agent_console.print(f"\n[bold]Q:[/bold] {question}")
        try:
            answer = Prompt.ask("  Your answer")
        except EOFError:
            answer = ""
        return answer.strip() or (
            "All 7 features must be fully implemented with passing tests. "
            "Every API endpoint must return the documented status codes. "
            "No feature may have partial implementation."
        )

    def derive_acceptance(
        self,
        functional_spec: str,
        api_contract: str,
        user_answer: str,
    ) -> list:
        """
        Phase 5 — Acceptance criteria derivation.

        Fresh LLM context. Reads 01_functional.md + 03_api_contract.md content
        (loaded from disk by the caller — never from Phase 4 memory).

        Returns list of AcceptanceCriteria Pydantic objects, one per feature.
        """
        user_content = (
            "Here is spec/01_functional.md:\n\n"
            f"{functional_spec}\n\n"
            "Here is spec/03_api_contract.md:\n\n"
            f"{api_contract}\n\n"
            f"User's definition of done: {user_answer}\n\n"
            "Derive acceptance criteria for every feature. Every edge case from "
            "01_functional.md must appear in the edge_case_coverage table. "
            "Pytest stub names must be scenario-specific, not generic."
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=_PHASE5_ACCEPTANCE_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        # Phase 5 generates a very large JSON block; the model sometimes fills
        # max_tokens with the JSON itself and omits the trailing marker.
        # We accept the response if the JSON block is present and parseable.
        has_json = "<ACCEPTANCE_JSON>" in response_text
        has_marker = "<<<PHASE_5_COMPLETE>>>" in response_text

        if not has_json and not has_marker:
            raise ValueError(
                "Phase 5: neither <ACCEPTANCE_JSON> block nor "
                "<<<PHASE_5_COMPLETE>>> marker found in LLM response.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_acceptance(response_text)

    def ask_architecture_questions(self, questions: list[str]) -> list[str]:
        """
        Present exactly 2 architecture questions to the user via Rich CLI.
        No LLM call. Returns list of answer strings in the same order.

        questions — exactly 2 items: [stack_question, constraints_question]
        """
        _agent_console.print(
            Panel(
                "[bold]Two quick questions before we derive the architecture.[/bold]\n"
                "[dim](Press Enter to accept the default shown.)[/dim]",
                title="[bold cyan]Architecture Questions[/bold cyan]",
                border_style="cyan",
            )
        )

        defaults = [
            "FastAPI + SQLAlchemy async / PostgreSQL / no queue / no frontend",
            "No additional hard constraints.",
        ]

        answers: list[str] = []
        for i, question in enumerate(questions, 1):
            _agent_console.print(f"\n[bold][{i}/{len(questions)}][/bold] {question}")
            try:
                answer = Prompt.ask("  Your answer")
            except EOFError:
                answer = ""
            answer = answer.strip() or defaults[i - 1]
            answers.append(answer)

        return answers

    def derive_components(
        self,
        product_spec: str,
        functional_spec: str,
        stack_answer: str = "",
        constraints_answer: str = "",
    ) -> tuple[list, "TechStack | None"]:
        """
        Phase 4 — Component architecture derivation.

        Reads 00_product.md + 01_functional.md content (fresh context — no Phase 3 memory).
        Also receives the user's stack and constraint answers to inform module design.

        Returns (components, stack) where:
          components — list of Component Pydantic objects
          stack      — TechStack parsed from the user's stack answer (or None on failure)
        """
        from devos.planning.spec_generator import Component, TechStack

        user_content = (
            "Here is 00_product.md:\n\n"
            f"{product_spec}\n\n"
            "Here is 01_functional.md:\n\n"
            f"{functional_spec}\n\n"
            f"User's tech stack answer: {stack_answer or 'Use sensible defaults.'}\n\n"
            f"User's hard constraints answer: {constraints_answer or 'None specified.'}\n\n"
            "Derive the complete module architecture. Every module must list the F-00X "
            "feature IDs it owns. Parse the tech stack from the user's answer."
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=_PHASE4_COMPONENTS_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        if "<<<PHASE_4_COMPONENTS_DERIVED>>>" not in response_text:
            raise ValueError(
                "Phase 4 components marker <<<PHASE_4_COMPONENTS_DERIVED>>> not found.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_components(response_text)

    def derive_constraints(
        self,
        product_spec: str,
        functional_spec: str,
        components: list,
        stack_answer: str = "",
        constraints_answer: str = "",
    ) -> "ArchConstraints":
        """
        Phase 4 — Constraints derivation.

        Fresh LLM context. Reads spec files + confirmed component list.
        Returns an ArchConstraints Pydantic object for .devos/constraints.md.
        """
        from devos.planning.spec_generator import ArchConstraints, Component

        components_summary = "\n".join(
            f"- {c.name}/: owns {c.owns} (features: {', '.join(c.features)})"
            for c in components
            if isinstance(c, Component)
        )

        user_content = (
            "Here is 00_product.md:\n\n"
            f"{product_spec}\n\n"
            "Here is 01_functional.md:\n\n"
            f"{functional_spec}\n\n"
            "Here are the confirmed modules:\n\n"
            f"{components_summary}\n\n"
            f"Tech stack: {stack_answer or 'FastAPI + SQLAlchemy async / PostgreSQL'}\n\n"
            f"User's hard constraints: {constraints_answer or 'None specified.'}\n\n"
            "Derive the constraints file content. Keep it SHORT — max 5 items per section."
        )

        message = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=_PHASE4_CONSTRAINTS_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = message.content[0].text

        if "<<<PHASE_4_CONSTRAINTS_DERIVED>>>" not in response_text:
            raise ValueError(
                "Phase 4 constraints marker <<<PHASE_4_CONSTRAINTS_DERIVED>>> not found.\n"
                f"Response was:\n{response_text}"
            )

        return self._parse_arch_constraints(response_text)

    def ask_schema_questions(self, questions: list[str]) -> list[str]:
        """
        Present gap questions to the user via Rich CLI and return their answers.

        questions — 0–4 strings produced by derive_data_model().
        Returns a list of answer strings in the same order.
        """
        if not questions:
            return []

        _agent_console.print(
            Panel(
                "[bold]A few schema decisions need your input.[/bold]\n"
                "Answer each question so the data model can be finalised.\n"
                "[dim](Press Enter to accept the suggested default if shown.)[/dim]",
                title="[bold cyan]Schema Questions[/bold cyan]",
                border_style="cyan",
            )
        )

        answers: list[str] = []
        for i, question in enumerate(questions, 1):
            _agent_console.print(f"\n[bold][{i}/{len(questions)}][/bold] {question}")
            try:
                answer = Prompt.ask("  Your answer")
            except EOFError:
                answer = "Use the most normalized approach (separate table with FK)."
            answers.append(answer)

        return answers

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_phase1_response(self, text: str) -> dict:
        """Extract the PRODUCT_JSON block from the LLM response."""
        match = re.search(
            r"<PRODUCT_JSON>(.*?)</PRODUCT_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <PRODUCT_JSON>...</PRODUCT_JSON> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse PRODUCT_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        required_keys = {
            "vision",
            "problem",
            "users",
            "in_scope",
            "out_of_scope",
            "success_criteria",
            "non_negotiables",
        }
        missing = required_keys - data.keys()
        if missing:
            raise ValueError(f"PRODUCT_JSON missing required keys: {missing}")

        return data

    def _parse_feature_list(self, text: str) -> list[dict]:
        """Extract the FEATURE_LIST JSON array from the LLM response."""
        match = re.search(
            r"<FEATURE_LIST>(.*?)</FEATURE_LIST>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <FEATURE_LIST>...</FEATURE_LIST> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse FEATURE_LIST JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError("FEATURE_LIST must be a JSON array")

        # Hard cap enforcement
        if len(data) > 8:
            data = data[:8]

        for item in data:
            if "id" not in item or "name" not in item:
                raise ValueError(f"Feature item missing required keys: {item}")

        return data

    def _parse_feature(self, text: str, feature_id: str, feature_name: str):
        """Extract the FEATURE_JSON object from the LLM response."""
        from devos.planning.spec_generator import Feature

        match = re.search(
            r"<FEATURE_JSON>(.*?)</FEATURE_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                f"Could not find <FEATURE_JSON>...</FEATURE_JSON> for {feature_id}.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse FEATURE_JSON for {feature_id}: {exc}\n"
                f"Raw JSON:\n{raw_json}"
            ) from exc

        # Ensure id and name are exactly what was requested
        data["id"] = feature_id
        data["name"] = feature_name

        required_keys = {
            "id",
            "name",
            "status",
            "trigger",
            "preconditions",
            "behavior",
            "edge_cases",
            "out_of_scope",
        }
        missing = required_keys - data.keys()
        if missing:
            raise ValueError(
                f"FEATURE_JSON for {feature_id} missing keys: {missing}"
            )

        return Feature(**data)

    def _parse_data_model(self, text: str) -> tuple[list, list[str]]:
        """Extract DATA_MODEL_JSON from the LLM response."""
        from devos.planning.spec_generator import Table

        match = re.search(
            r"<DATA_MODEL_JSON>(.*?)</DATA_MODEL_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <DATA_MODEL_JSON>...</DATA_MODEL_JSON> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse DATA_MODEL_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        if "tables" not in data:
            raise ValueError("DATA_MODEL_JSON missing required key: 'tables'")

        tables: list[Table] = []
        for t in data["tables"]:
            t.setdefault("out_of_scope", [])
            tables.append(Table(**t))

        gap_questions: list[str] = data.get("gap_questions", [])
        if not isinstance(gap_questions, list):
            gap_questions = []
        # Hard cap: never more than 4
        gap_questions = gap_questions[:4]

        return tables, gap_questions

    def _parse_api_contract(self, text: str) -> list:
        """Extract API_CONTRACT_JSON from the LLM response."""
        from devos.planning.spec_generator import Endpoint

        match = re.search(
            r"<API_CONTRACT_JSON>(.*?)</API_CONTRACT_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <API_CONTRACT_JSON>...</API_CONTRACT_JSON> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse API_CONTRACT_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        if "endpoints" not in data:
            raise ValueError("API_CONTRACT_JSON missing required key: 'endpoints'")

        endpoints: list[Endpoint] = []
        for ep in data["endpoints"]:
            ep.setdefault("purpose", "")
            ep.setdefault("out_of_scope", [])
            ep.setdefault("request_body", None)
            endpoints.append(Endpoint(**ep))

        return endpoints

    def _parse_components(self, text: str) -> tuple[list, "TechStack | None"]:
        """Extract COMPONENTS_JSON from the LLM response."""
        from devos.planning.spec_generator import Component, TechStack

        match = re.search(
            r"<COMPONENTS_JSON>(.*?)</COMPONENTS_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <COMPONENTS_JSON>...</COMPONENTS_JSON> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse COMPONENTS_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        if "components" not in data:
            raise ValueError("COMPONENTS_JSON missing required key: 'components'")

        components: list[Component] = []
        for c in data["components"]:
            c.setdefault("internal_structure", [])
            c.setdefault("interfaces_exposed", [])
            c.setdefault("may_import", [])
            c.setdefault("must_never_import", [])
            c.setdefault("features", [])
            components.append(Component(**c))

        stack: TechStack | None = None
        if "stack" in data and isinstance(data["stack"], dict):
            try:
                sd = data["stack"]
                sd.setdefault("extras", [])
                sd.setdefault("frontend", None)
                sd.setdefault("queue", None)
                stack = TechStack(**sd)
            except Exception:
                stack = None

        return components, stack

    def _parse_acceptance(self, text: str) -> list:
        """Extract ACCEPTANCE_JSON from the LLM response.

        Handles two cases:
        1. Clean response with closing </ACCEPTANCE_JSON> tag.
        2. Truncated response where max_tokens cut off the closing tag — we
           attempt to recover by finding the opening tag and trimming to the
           last complete JSON object.
        """
        from devos.planning.spec_generator import (
            AcceptanceCriteria, PytestStub, EdgeCaseMapping
        )

        match = re.search(
            r"<ACCEPTANCE_JSON>(.*?)</ACCEPTANCE_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            # Attempt recovery: find the opening tag and parse up to the last ']'
            open_match = re.search(r"<ACCEPTANCE_JSON>(.*)", text, re.DOTALL)
            if not open_match:
                raise ValueError(
                    "Could not find <ACCEPTANCE_JSON> opening tag in LLM response.\n"
                    f"Response was:\n{text[:500]}"
                )
            raw = open_match.group(1)
            # Trim to the last complete top-level object by finding the last ']'
            last_bracket = raw.rfind("]")
            if last_bracket == -1:
                raise ValueError(
                    "ACCEPTANCE_JSON: no closing ']' found even in recovery mode.\n"
                    f"Partial content was:\n{raw[:500]}"
                )
            raw_json = raw[: last_bracket + 1].strip()
        else:
            raw_json = match.group(1).strip()

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse ACCEPTANCE_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError("ACCEPTANCE_JSON must be a JSON array")

        criteria: list[AcceptanceCriteria] = []
        for item in data:
            stubs = [
                PytestStub(name=s["name"], docstring=s["docstring"])
                for s in item.get("pytest_stubs", [])
            ]
            edge_coverage = [
                EdgeCaseMapping(
                    edge_case=m["edge_case"],
                    test=m["test"],
                )
                for m in item.get("edge_case_coverage", [])
            ]
            criteria.append(
                AcceptanceCriteria(
                    feature_id=item["feature_id"],
                    feature_name=item["feature_name"],
                    done_criteria=item.get("done_criteria", []),
                    pytest_stubs=stubs,
                    edge_case_coverage=edge_coverage,
                )
            )

        return criteria

    def _parse_arch_constraints(self, text: str) -> "ArchConstraints":
        """Extract CONSTRAINTS_JSON from the LLM response."""
        from devos.planning.spec_generator import ArchConstraints

        match = re.search(
            r"<CONSTRAINTS_JSON>(.*?)</CONSTRAINTS_JSON>",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(
                "Could not find <CONSTRAINTS_JSON>...</CONSTRAINTS_JSON> in LLM response.\n"
                f"Response was:\n{text}"
            )

        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Failed to parse CONSTRAINTS_JSON: {exc}\nRaw JSON:\n{raw_json}"
            ) from exc

        required = {"hard_rules", "naming", "always_used"}
        missing = required - data.keys()
        if missing:
            raise ValueError(f"CONSTRAINTS_JSON missing required keys: {missing}")

        data.setdefault("non_functional", [])
        return ArchConstraints(**data)
