#!/usr/bin/env python3
"""
Phase 2 pre-flip checks.

Run from the project root:
    python scripts/phase2_preflight.py

Steps:
  1. Backfill creator_tokens from token_analysis.pf_ws_creator
  2. Reconcile token_count_seen in creator_profile
  3. Check bootstrap coverage (need >= 95% before flipping)
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Main app DB — contains token_analysis, creator_funders, etc.
MAIN_DB_PATH    = "database/flex_complete_database.db"
# Creator pipeline DB — contains creator_profile, creator_tokens, etc.
CREATOR_DB_PATH = "pumpswap_tokens.db"


async def main():
    from src.creators.migration_bridge import backfill_creator_tokens, validate_bootstrap_coverage
    from src.creators.repository import CreatorRepository

    db_lock = asyncio.Lock()

    print("=== Initialising schema ===")
    repo = CreatorRepository(db_path=CREATOR_DB_PATH, db_lock=db_lock)
    await repo.ensure_schema()
    print("  done")

    print("\n=== Step 0: Bootstrap creator_profile from creator_funders ===")
    from src.creators.migration_bridge import bootstrap_creator_profiles
    bootstrapped = await bootstrap_creator_profiles(
        repo,
        db_path=MAIN_DB_PATH,            # reads creator_funders from main DB
        creator_db_path=CREATOR_DB_PATH, # writes creator_profile to creator DB
        db_lock=db_lock,
        batch_size=5000,
    )
    print(f"  profiles upserted: {bootstrapped}")

    print("\n=== Step 1: Backfill creator_tokens ===")
    result = await backfill_creator_tokens(
        db_path=CREATOR_DB_PATH,
        source_db_path=MAIN_DB_PATH,
        db_lock=db_lock,
    )
    print(f"  links_inserted:   {result['links_inserted']}")
    print(f"  profiles_updated: {result['profiles_updated']}")

    print("\n=== Step 2: Bootstrap coverage check ===")
    coverage = await validate_bootstrap_coverage(
        db_path=MAIN_DB_PATH,
        creator_db_path=CREATOR_DB_PATH,
        db_lock=db_lock,
    )
    print(f"  total_creators:       {coverage['total_creators_in_funders']}")
    print(f"  profiles_non_unknown: {coverage['profiles_non_unknown']}")
    print(f"  coverage_pct:         {coverage['coverage_pct']}%")
    print(f"  safe_to_activate:     {coverage['safe_to_activate']}")

    if coverage['safe_to_activate']:
        print("\n✓ Ready to flip. Run:")
        print("  sqlite3 pumpswap_tokens.db \"UPDATE system_config SET value='true', updated_at=strftime('%s','now') WHERE key='phase_2_active';\"")
    else:
        print(f"\n✗ Coverage too low ({coverage['coverage_pct']}%). Wait for baseline worker to catch up.")


if __name__ == "__main__":
    asyncio.run(main())
