"""Compact token market-class utility.

The legacy price-history behaviour classifier was retired. Current callers use
only the retained peak-market-cap grade below; no dense price-history or
behaviour persistence is retained here.
"""

G1_MC = 5_000_000
G2_MC = 2_000_000
G3_MC = 500_000
G4_MC = 300_000
G5_MC = 150_000
G6_MC = 75_000


def compute_token_class(peak_market_cap_usd: float) -> str:
    """Assign a G-class from a retained peak market-cap fact alone."""
    if peak_market_cap_usd <= 0:
        return "G?"
    if peak_market_cap_usd >= G1_MC:
        return "G1"
    if peak_market_cap_usd >= G2_MC:
        return "G2"
    if peak_market_cap_usd >= G3_MC:
        return "G3"
    if peak_market_cap_usd >= G4_MC:
        return "G4"
    if peak_market_cap_usd >= G5_MC:
        return "G5"
    if peak_market_cap_usd >= G6_MC:
        return "G6"
    return "G7"
