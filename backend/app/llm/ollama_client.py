"""
Ollama client + GM generation loop.

Talks to a local Ollama server (default http://localhost:11434) which serves the
7B models. Uses the chat API with `format="json"` to nudge the model toward
valid JSON, then runs the schema validate/repair loop.
"""

from __future__ import annotations

import httpx

from app.agents.gm_prompt import (
    GM_SYSTEM_PROMPT,
    build_gm_user_prompt,
    gm_schema_as_text,
)
from app.llm.json_repair import ParseResult, parse_and_validate
from app.schemas.game_setting import GameSetting

DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaClient:
    """Thin async wrapper over the Ollama chat endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        format_schema: dict | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Send a chat request; return the assistant message content.

        When ``format_schema`` (a JSON Schema dict, e.g. ``Model.model_json_schema()``)
        is supplied, Ollama constrains decoding so every sampled token conforms to
        the schema — grammar-based structured generation. This guarantees
        syntactically valid, shape-correct JSON (no dropped commas / creative
        arrays), so the model can never derail the loop with malformed output, and
        the text JSON schema can be dropped from the prompt. ``json_mode`` is the
        looser fallback (free-form JSON) kept for the GM path.
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if format_schema is not None:
            payload["format"] = format_schema
        elif json_mode:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data["message"]["content"]


async def list_installed_models(
    base_url: str = DEFAULT_OLLAMA_URL, *, timeout: float = 10.0
) -> list[str]:
    """Return the model names installed on the local Ollama server.

    Best-effort: returns an empty list if Ollama is unreachable so the UI can
    fall back to a free-text model field instead of failing.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []

    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
    return sorted(set(models))


async def generate_blueprint(
    client: OllamaClient,
    *,
    theme: str,
    num_players: int,
    difficulty: str = "medium",
    max_attempts: int = 3,
    temperature: float = 0.6,
) -> ParseResult[GameSetting]:
    """
    Ask the GM model to produce a valid GameSetting, retrying with repair
    instructions on schema failures.
    """
    system = (
        GM_SYSTEM_PROMPT
        + "\n\nThe JSON Schema you MUST satisfy:\n"
        + gm_schema_as_text()
    )
    user = build_gm_user_prompt(
        theme=theme, num_players=num_players, difficulty=difficulty
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    last: ParseResult[GameSetting] = ParseResult(ok=False, error="no attempt made")
    for attempt in range(1, max_attempts + 1):
        raw = await client.chat(messages, json_mode=True, temperature=temperature)
        result = parse_and_validate(raw, GameSetting)
        if result.ok:
            return result

        last = result
        # Feed the failed output + repair instruction back for another try.
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": result.repair_instruction or "Return valid JSON only.",
            }
        )

    return last
