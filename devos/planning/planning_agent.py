"""LLM calls for each interview phase."""

from __future__ import annotations

import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

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
