"""
CLI tool for generating storyboards from world JSON files.

Usage:
  python -m app.tools.generate_storyboard world_034
  python -m app.tools.generate_storyboard world_034 --dry-run
  python -m app.tools.generate_storyboard world_034 --force
  python -m app.tools.generate_storyboard --all
  python -m app.tools.generate_storyboard --all --force
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

GAME_DIR = Path(__file__).parent.parent / "game"


async def generate_one(
    world_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    base_url: str | None = None,
) -> bool:
    """Generate a storyboard for one world. Returns True on success."""
    from app.agents.storyboard import is_storyboard_stale, storyboard_path_for
    from app.agents.storyboard_generator import StoryboardGenerator, make_storyboard_client
    from app.schemas.fixed_world import load_setting_compat

    world_path = GAME_DIR / f"{world_id}.json"
    if not world_path.exists():
        print(f"  ERROR: {world_path} not found.")
        return False

    sb_path = storyboard_path_for(world_path)

    if sb_path.exists() and not force and not is_storyboard_stale(world_path, sb_path):
        print(f"  {world_id}: storyboard already up-to-date. Use --force to regenerate.")
        return True

    print(f"  {world_id}: loading world...", end=" ", flush=True)
    try:
        with world_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        setting = load_setting_compat(payload)
    except Exception as exc:
        print(f"FAILED\n  ERROR loading world: {exc}")
        return False

    print("generating storyboard...", end=" ", flush=True)
    client = make_storyboard_client(base_url)
    generator = StoryboardGenerator(client)

    t0 = time.monotonic()
    storyboard = await generator.generate(setting, world_id=world_id)
    elapsed = time.monotonic() - t0

    if storyboard.is_empty():
        print(f"FAILED (LLM returned empty storyboard)")
        return False

    if dry_run:
        print(f"OK ({elapsed:.1f}s) — dry run, not saving")
        print(json.dumps(storyboard.to_dict(), indent=2, ensure_ascii=False))
        return True

    storyboard.save(sb_path)
    print(f"OK ({elapsed:.1f}s) → {sb_path.name}")
    return True


async def run(args: argparse.Namespace) -> int:
    base_url = args.base_url if hasattr(args, "base_url") else None

    if args.all:
        world_files = sorted(GAME_DIR.glob("world_*.json"))
        # Exclude storyboard files themselves.
        world_ids = [
            f.stem for f in world_files
            if not f.stem.endswith("_storyboard")
        ]
        if not world_ids:
            print(f"No world_*.json files found in {GAME_DIR}")
            return 1

        print(f"Generating storyboards for {len(world_ids)} worlds...")
        failures = 0
        for wid in world_ids:
            ok = await generate_one(wid, force=args.force, dry_run=args.dry_run, base_url=base_url)
            if not ok:
                failures += 1
        print(f"\nDone. {len(world_ids) - failures}/{len(world_ids)} succeeded.")
        return 0 if failures == 0 else 1

    else:
        world_id = args.world_id
        ok = await generate_one(world_id, force=args.force, dry_run=args.dry_run, base_url=base_url)
        return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate narrative storyboards for escape room world JSON files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "world_id",
        nargs="?",
        help="World ID to generate (e.g. world_034). Omit when using --all.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate storyboards for all world_*.json files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if a storyboard already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated storyboard to stdout without saving.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Ollama server URL (default: http://localhost:11434).",
    )

    args = parser.parse_args()

    if not args.all and not args.world_id:
        parser.error("Provide a world_id or use --all.")

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
