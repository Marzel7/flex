"""Small fail-closed event-level mint guard for Byzantine Actual Entry V2."""
from __future__ import annotations

PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

def resolve_event_mint(event: dict, target_mint: str) -> dict:
    """Return MATCH only for a normalized economic event with linked token side."""
    if event.get("program_id") != PUMPSWAP or event.get("event_type") not in {"BUY", "SELL"}:
        return {"state":"UNPROVEN"}
    mint = event.get("event_mint")
    sides = set(event.get("token_side_mints") or [])
    if not mint or mint not in sides:
        return {"state":"UNPROVEN"}
    return {"state":"MATCH" if mint == target_mint else "NON_MATCH", "event_mint":mint,
            "target_side":"TOKEN" if mint == target_mint else None}

def select_earliest_target_event(events: list[dict], target_mint: str) -> dict | None:
    for event in sorted(events, key=lambda e: (int(e.get("slot", 0)), int(e.get("instruction_index", 0)))):
        result=resolve_event_mint(event,target_mint)
        if result["state"]=="MATCH": return {**event,"mint_match":result}
    return None
