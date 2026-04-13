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
