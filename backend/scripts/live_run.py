"""
Live end-to-end run of a game against a local Ollama server (Phase 6).

Drives the real GamePlayerAgents (no scripted clients) and prints the streamed
narrative + a final summary. Useful for observing 7B reliability and tuning
prompts.

Usage (from backend/):
    ../.venv/bin/python -m scripts.live_run --model qwen2.5:7b --rounds 24
"""

from __future__ import annotations

import argparse
import asyncio

from app.game.orbital_decay_setting import ORBITAL_DECAY_SETTING
from app.schemas.game_setting import GameSetting
from app.web.runner import GameRunner, build_agents, build_narrator


async def _main(model: str, rounds: int, base_url: str | None, narrate: bool) -> int:
    setting = GameSetting.model_validate(ORBITAL_DECAY_SETTING)
    agents = build_agents(setting, model=model, base_url=base_url)
    narrator = build_narrator(model=model, base_url=base_url) if narrate else None
    runner = GameRunner(setting, agents, max_rounds=rounds, narrator=narrator)

    won = {"value": False}

    async def send(msg: dict) -> None:
        t = msg["type"]
        if t == "setup":
            print(f"\n=== {msg['objective']} ===")
            print("Crew:", ", ".join(f"{p['name']} ({p['role']})" for p in msg["players"]))
            print("-" * 60)
        elif t == "event":
            if msg["kind"] == "narration":
                # The story prose — the headline for the human audience.
                print(f"\n  \u201c{msg['text']}\u201d\n")
                return
            tag = {"speech": "SAY", "observation": "OBS", "system": "SYS"}[msg["kind"]]
            who = msg["actor_id"] or "narrator"
            vis = "" if msg["public"] else " (private)"
            print(f"[t{msg['turn']:>2}] {tag:<3} {who}{vis}: {msg['text']}")
        elif t == "result":
            won["value"] = msg["won"]
            print("-" * 60)
            verdict = "ESCAPED 🎉" if msg["won"] else "did NOT escape"
            print(f"RESULT: team {verdict} — {msg['reason']} in {msg['turns']} turns.")
        elif t == "error":
            print(f"ERROR: {msg['message']}")

    await runner.run(send)
    return 0 if won["value"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Ollama game run.")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--no-narrate",
        action="store_true",
        help="Disable the GM storyteller (structured events only).",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(_main(args.model, args.rounds, args.base_url, not args.no_narrate))
    )


if __name__ == "__main__":
    main()
