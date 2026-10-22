import json
import os
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You qualify residential solar leads for Acme Solar, which serves Austin and Central Texas.
Score the lead from 0 to 100 for how likely a 20-minute consultation leads to an installation.
Weigh, in this order: owns the home; average monthly power bill (under 80 USD is a weak fit,
over 150 USD is strong); roof shade (heavy shade is a weak fit); timeline (ASAP is strongest);
ZIP code inside the service area (Austin ZIP codes start with 787).
Give two to four short reasons. Write the summary as one sentence a sales rep can read in five seconds.
Use only facts in the lead. Never invent details."""

# Claude returns the score and the words; code applies the tier thresholds (business rules, pinned by tests).
CLAUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["score", "reasons", "summary"],
    "additionalProperties": False,
}


class ClaudeVerdict(BaseModel):
    score: int = Field(ge=0, le=100)
    reasons: List[str]
    summary: str


class Qualification(BaseModel):
    score: int
    tier: Literal["hot", "warm", "cold"]
    reasons: List[str]
    summary: str
    source: Literal["claude", "rules"]


client = anthropic.Anthropic(timeout=8.0, max_retries=1)


def tier_for(score: int) -> str:
    if score >= 75:
        return "hot"
    if score >= 45:
        return "warm"
    return "cold"


def qualify_with_claude(lead: dict) -> Qualification:
    # Only the facts the score needs go to Claude: no name, email or phone.
    facts = {key: lead.get(key) for key in ("homeowner", "monthly_bill", "roof_shade", "timeline", "postal_code")}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Without a key the SDK raises a TypeError the fallback below wouldn't catch.
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4096,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts)}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped with {response.stop_reason}")
    text = next(block.text for block in response.content if block.type == "text")
    verdict = ClaudeVerdict.model_validate_json(text)
    return Qualification(tier=tier_for(verdict.score), source="claude", **verdict.model_dump())


def qualify_with_rules(lead: dict) -> Qualification:
    score, reasons = 0, []
    if lead["homeowner"]:
        score += 40
        reasons.append("Owns the home")
    else:
        reasons.append("Not the homeowner")
    bill = lead["monthly_bill"]
    if bill >= 150:
        score += 30
        reasons.append(f"High power bill ({bill:.0f} USD)")
    elif bill >= 80:
        score += 15
        reasons.append(f"Average power bill ({bill:.0f} USD)")
    score += {"None": 15, "Some": 7}.get(lead["roof_shade"], 0)
    score += {"ASAP": 15, "1 to 3 months": 8}.get(lead["timeline"], 0)
    summary = f"{lead['first_name']}: {', '.join(reasons).lower()}, scored by rules."
    return Qualification(score=score, tier=tier_for(score), reasons=reasons, summary=summary, source="rules")


def qualify(lead: dict) -> Qualification:
    try:
        return qualify_with_claude(lead)
    except (anthropic.APIError, RuntimeError, ValidationError, StopIteration) as exc:
        result = qualify_with_rules(lead)
        result.reasons.append(f"Claude unavailable ({type(exc).__name__}), rules used")
        return result
