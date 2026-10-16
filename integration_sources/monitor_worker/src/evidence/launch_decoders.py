"""Operation-neutral decoders for objectively observable launch instructions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_decode(value: str) -> bytes:
    number = 0
    for character in value:
        number = number * 58 + _BASE58.index(character)
    encoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + encoded


def _anchor_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


@dataclass(frozen=True)
class DecodedLaunch:
    event_type: str
    mint: str
    creator: str
    source_platform: str
    decoder_version: str


class LaunchInstructionDecoder:
    """Registry boundary. Decoders identify launches, never operational identity."""

    VERSION = "1"

    _PUMP_LAYOUTS = {
        _anchor_discriminator("create"): ("create", 0, 7),
        _anchor_discriminator("create_v2"): ("create_v2", 0, 5),
    }

    @staticmethod
    def _accounts(item: Mapping[str, Any], transaction_accounts: Sequence[Optional[str]]) -> list[Optional[str]]:
        result: list[Optional[str]] = []
        for value in item.get("accounts") or ():
            if isinstance(value, int):
                result.append(transaction_accounts[value] if value < len(transaction_accounts) else None)
            elif isinstance(value, str):
                result.append(value)
            else:
                result.append(None)
        return result

    def decode(self, *, program_id: Any, item: Mapping[str, Any],
               transaction_accounts: Sequence[Optional[str]]) -> Optional[DecodedLaunch]:
        if program_id != PUMP_PROGRAM or not isinstance(item.get("data"), str):
            return None
        try:
            discriminator = _base58_decode(item["data"])[:8]
        except (ValueError, IndexError):
            return None
        layout = self._PUMP_LAYOUTS.get(discriminator)
        if layout is None:
            return None
        event_type, mint_index, creator_index = layout
        accounts = self._accounts(item, transaction_accounts)
        if max(mint_index, creator_index) >= len(accounts):
            return None
        mint, creator = accounts[mint_index], accounts[creator_index]
        if not isinstance(mint, str) or not isinstance(creator, str):
            return None
        return DecodedLaunch(
            event_type=event_type, mint=mint, creator=creator,
            source_platform=str(program_id), decoder_version=self.VERSION,
        )
