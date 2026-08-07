"""Closed Primitive Contract v1 registry."""

from __future__ import annotations

from .contracts import PrimitiveType


class PrimitiveRegistry:
    VERSION = "1"
    _TYPES = tuple(PrimitiveType)

    @classmethod
    def types(cls) -> tuple[PrimitiveType, ...]:
        return cls._TYPES

    @classmethod
    def contains(cls, value: str) -> bool:
        return any(item.value == value for item in cls._TYPES)

    @classmethod
    def deferred_candidates(cls) -> tuple[str, ...]:
        return (
            "TOKEN_TRANSFER", "ACCOUNT_CREATION", "TRANSACTION_SIGNER",
            "FEE_PAYER", "LAUNCH_CREATOR", "ACCOUNT_CLOSE", "PROGRAM_REUSE",
        )
