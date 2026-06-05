"""Diagnostic live run: capture a compact per-turn transcript of a world file.

Usage (from backend/):
    ../.venv/bin/python -m scripts.diagnose_run --world world_020 --rounds 14
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.context.channels import Event, EventKind
from app.schemas.fixed_world import load_setting_compat
from app.web.runner import build_agents
from app.orchestrator.loop import GameOrchestrator


async def _main(world: str, rounds: int, model: str) -> int:
    data = json.loads(Path(f"app/game/{world}.json").read_text())
    setting = load_setting_compat(data)
    agents = build_agents(setting, model=model, temperature=0.4)

    captured: list[Event] = []

    async def on_event(event: Event) -> None:
        captured.append(event)

    orch = GameOrchestrator(
        setting,
        agents,
        max_rounds=rounds,
        on_event=on_event,
        narrator=None,
        enforce_candidate_policy=False,
    )
    result = await orch.run()

    # Compact transcript: skip PROMPT (huge) but keep one sample.
    sample_prompt = None
    for ev in captured:
        kind = ev.kind.value if isinstance(ev.kind, EventKind) else ev.kind
        if kind == "prompt" and sample_prompt is None:
            sample_prompt = ev.text
        if kind == "prompt":
            continue
        actor = ev.actor_id or "-"
        text = (ev.text or "").replace("\n", " ⏎ ")
        if len(text) > 240:
            text = text[:240] + "…"
        print(f"[t{ev.turn:>2}] {kind:<11} {actor:<10} {text}")

    print("\n" + "=" * 70)
    print(f"RESULT won={result.won} reason={result.reason} turns={result.turns}")
    print("=" * 70)
    if sample_prompt:
        print("\n----- SAMPLE PROMPT (first player turn) -----\n")
        print(sample_prompt)
    return 0 if result.won else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--world", default="world_020")
    p.add_argument("--rounds", type=int, default=14)
    p.add_argument("--model", default="qwen2.5:7b")
    args = p.parse_args()
    raise SystemExit(asyncio.run(_main(args.world, args.rounds, args.model)))


if __name__ == "__main__":
    main()
