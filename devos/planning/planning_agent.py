"""LLM calls for each interview phase."""

from __future__ import annotations

import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

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
