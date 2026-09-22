"""Fail-closed research classification for ByZc creator-funding evidence."""
from __future__ import annotations

WSOL = "WSOL_WRAP_CLOSE_PROVEN"
PLAIN = "PLAIN_XFER_PROVEN"
OTHER = "OTHER_MECHANISM_PROVEN"
INSUFFICIENT = "MECHANISM_INSUFFICIENT_EVIDENCE"
CONTRACT = "BYZC_CREATOR_FUNDING_MECHANISM_V2"


def classify(*, wsol_atomic: bool, wsol_close_to_creator: bool,
             plain_system_transfer: bool, plain_destination_is_creator: bool) -> str:
    """Every proven mechanism requires positive, mint-coupled evidence."""
    if wsol_atomic and wsol_close_to_creator:
        return WSOL
    if plain_system_transfer and plain_destination_is_creator:
        return PLAIN
    return INSUFFICIENT
