"""
JSON parsing + schema validation + repair loop.

Local 7B models frequently wrap JSON in prose or code fences, or emit a slightly
malformed blueprint. This module:
  1. Extracts the most likely JSON object from raw model text.
  2. Validates it against a Pydantic model.
  3. On failure, produces a structured `repair_instruction` the orchestrator can
     feed back to the model for another attempt.

The actual LLM call lives in the Ollama client; this module is pure/synchronous
and unit-testable without a running model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Generic, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


@dataclass
class ParseResult(Generic[T]):
    """Outcome of attempting to parse model output into a schema."""

    ok: bool
    value: Optional[T] = None
    error: Optional[str] = None
    repair_instruction: Optional[str] = None


def extract_json_object(raw: str) -> Optional[str]:
    """
    Best-effort extraction of a single JSON object from raw model text.

    Handles: code fences (```json ... ```), leading/trailing prose, and picks
    the substring between the first '{' and its matching closing '}'.
    """
    if not raw:
        return None

    text = raw.strip()

    # Strip code fences if present.
    if "```" in text:
        # Take content inside the first fenced block.
        parts = text.split("```")
        for part in parts:
            candidate = part
            if candidate.lstrip().lower().startswith("json"):
                candidate = candidate.lstrip()[4:]
            candidate = candidate.strip()
            if candidate.startswith("{"):
                text = candidate
                break

    start = text.find("{")
    if start == -1:
        return None

    # Walk to the matching closing brace, respecting strings/escapes.
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_and_validate(raw: str, schema: Type[T]) -> ParseResult[T]:
    """Extract JSON from `raw`, parse it, and validate against `schema`."""
    candidate = extract_json_object(raw)
    if candidate is None:
        return ParseResult(
            ok=False,
            error="No JSON object found in model output.",
            repair_instruction=(
                "Your previous reply contained no JSON object. Reply with ONLY a "
                "single valid JSON object that matches the schema — no prose, no "
                "code fences."
            ),
        )

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return ParseResult(
            ok=False,
            error=f"JSON decode error: {exc}",
            repair_instruction=(
                f"Your JSON was malformed ({exc.msg} at line {exc.lineno} "
                f"col {exc.colno}). Return a corrected, complete JSON object only."
            ),
        )

    try:
        value = schema.model_validate(data)
    except ValidationError as exc:
        return ParseResult(
            ok=False,
            error=str(exc),
            repair_instruction=(
                "Your JSON did not satisfy the schema. Fix EXACTLY these problems "
                "and return the full corrected JSON only:\n"
                + _summarize_validation_error(exc)
            ),
        )

    return ParseResult(ok=True, value=value)


def _summarize_validation_error(exc: ValidationError) -> str:
    """Turn a Pydantic ValidationError into compact, model-friendly bullets."""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        lines.append(f"- field '{loc}': {msg}")
    return "\n".join(lines[:20])  # cap to keep the repair prompt small
