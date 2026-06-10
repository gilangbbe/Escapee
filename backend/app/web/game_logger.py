"""
Game session logger.

Writes two files per session to backend/logs/:
  game_<timestamp>.jsonl   — one raw JSON dict per line (every WebSocket message)
  game_<timestamp>.txt     — human-readable transcript mirroring what the UI shows

Usage: wrap the WebSocket `send` callback with `GameLogger.wrap(send)`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

LOGS_DIR = Path(__file__).parent.parent.parent / "logs"

# Event kinds the narrator renders visibly in the UI (mirrors NarrativeFeed.tsx)
_VISIBLE_KINDS = {"speech", "observation", "system", "narration"}
_SKIP_KINDS = {"planner", "prompt", "decision"}


def _format_event(msg: dict) -> str | None:
    """Return a single human-readable line for a visible event, or None to skip."""
    kind = msg.get("kind", "")
    if kind in _SKIP_KINDS:
        return None

    turn = msg.get("turn", 0)
    text = (msg.get("text") or "").strip()
    actor = msg.get("actor_id") or ""
    is_public = msg.get("public", True)

    if kind == "narration":
        return f"\n[NARRATOR] {text}\n"

    if kind == "system":
        return f"[T{turn:>2} SYS] {text}"

    if kind == "observation":
        scope = "PUB" if is_public else "PRV"
        who = f"{actor} " if actor else ""
        return f"[T{turn:>2} OBS {scope}] {who}{text}"

    if kind == "speech":
        who = actor or "?"
        return f"[T{turn:>2} {who}] {text}"

    return None


class GameLogger:
    def __init__(self, world_id: str = "") -> None:
        LOGS_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = f"_{world_id}" if world_id else ""
        base = LOGS_DIR / f"game{tag}_{stamp}"
        self._jsonl_path = base.with_suffix(".jsonl")
        self._txt_path = base.with_suffix(".txt")
        self._jsonl = open(self._jsonl_path, "w", encoding="utf-8")
        self._txt = open(self._txt_path, "w", encoding="utf-8")
        self._txt.write(f"=== GAME SESSION {stamp}{tag} ===\n\n")

    def record(self, message: dict) -> None:
        self._jsonl.write(json.dumps(message, ensure_ascii=False) + "\n")
        self._jsonl.flush()

        t = message.get("type")
        if t == "setup":
            players = ", ".join(
                f"{p['name']} ({p.get('role', '?')})" for p in message.get("players", [])
            )
            world = message.get("scenario", "")[:120]
            self._txt.write(f"SCENARIO: {world}\nCAST: {players}\n{'─'*60}\n\n")
        elif t == "event":
            line = _format_event(message)
            if line:
                self._txt.write(line + "\n")
        elif t == "result":
            won = "ESCAPED 🎉" if message.get("won") else "FAILED ✗"
            self._txt.write(
                f"\n{'─'*60}\nRESULT: {won} in {message.get('turns', '?')} turns.\n"
            )
        elif t == "error":
            self._txt.write(f"\n[ERROR] {message.get('message')}\n")

        self._txt.flush()

    def close(self) -> None:
        self._jsonl.close()
        self._txt.close()

    @property
    def txt_path(self) -> Path:
        return self._txt_path

    def wrap(
        self, send: Callable[[dict], Awaitable[None]], world_id: str = ""
    ) -> Callable[[dict], Awaitable[None]]:
        """Return a new `send` that logs every message before forwarding it."""
        logger = GameLogger(world_id)

        async def _send(msg: dict) -> None:
            logger.record(msg)
            await send(msg)

        # Attach close so callers can finalize.
        _send._logger = logger  # type: ignore[attr-defined]
        return _send
