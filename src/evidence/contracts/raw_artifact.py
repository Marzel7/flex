"""EP1.3B exact-provider and historical representation contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .identity import _digest, _nonempty


class ArtifactRepresentation(str, Enum):
    EXACT_PROVIDER_ARTIFACT = "EXACT_PROVIDER_ARTIFACT"
    CANONICALIZED_RESPONSE_REPRESENTATION = "CANONICALIZED_RESPONSE_REPRESENTATION"
    RAW_BYTES_UNAVAILABLE = "RAW_BYTES_UNAVAILABLE"


_SECRET_PARAMETER_NAMES = {"api-key", "apikey", "api_key", "key", "token"}


@dataclass(frozen=True)
class RawArtifact:
    artifact_digest: str
    media_type: str
    compression: str
    encrypted: bool
    byte_length: int
    provider: str
    endpoint: str
    request_parameters_digest: str
    response_status: int
    acquired_at: int
    payload: Optional[bytes]
    representation: ArtifactRepresentation

    def __post_init__(self) -> None:
        if not isinstance(self.representation, ArtifactRepresentation):
            raise TypeError("representation must be an ArtifactRepresentation")
        _digest("artifact_digest", self.artifact_digest)
        _digest("request_parameters_digest", self.request_parameters_digest)
        for name in ("media_type", "compression", "provider", "endpoint"):
            _nonempty(name, str(getattr(self, name)))
        if not isinstance(self.encrypted, bool):
            raise TypeError("encrypted must be boolean")
        if not isinstance(self.byte_length, int) or self.byte_length < 0:
            raise ValueError("byte_length must be a non-negative integer")
        if not isinstance(self.response_status, int) or self.response_status < 0:
            raise ValueError("response_status must be a non-negative integer")
        if not isinstance(self.acquired_at, int) or self.acquired_at < 0:
            raise ValueError("acquired_at must be a non-negative Unix timestamp")
        lowered = self.endpoint.lower()
        if any(f"{name}=" in lowered for name in _SECRET_PARAMETER_NAMES):
            raise ValueError("endpoint contains a prohibited credential parameter")
        if self.representation is ArtifactRepresentation.RAW_BYTES_UNAVAILABLE:
            if self.payload is not None:
                raise ValueError("RAW_BYTES_UNAVAILABLE cannot contain fabricated payload bytes")
            if self.byte_length != 0:
                raise ValueError("RAW_BYTES_UNAVAILABLE must have byte_length=0")
        else:
            if not isinstance(self.payload, bytes):
                raise TypeError("retained artifact payload must be bytes")
            if len(self.payload) != self.byte_length:
                raise ValueError("byte_length does not match payload")
            if hashlib.sha256(self.payload).hexdigest() != self.artifact_digest:
                raise ValueError("artifact_digest does not match payload bytes")

    @property
    def satisfies_exact_replay_contract(self) -> bool:
        return self.representation is ArtifactRepresentation.EXACT_PROVIDER_ARTIFACT

    @classmethod
    def from_exact_bytes(
        cls,
        payload: bytes,
        *,
        media_type: str,
        compression: str,
        encrypted: bool,
        provider: str,
        endpoint: str,
        request_parameters_digest: str,
        response_status: int,
        acquired_at: int,
    ) -> "RawArtifact":
        return cls(
            artifact_digest=hashlib.sha256(payload).hexdigest(),
            media_type=media_type,
            compression=compression,
            encrypted=encrypted,
            byte_length=len(payload),
            provider=provider,
            endpoint=endpoint,
            request_parameters_digest=request_parameters_digest,
            response_status=response_status,
            acquired_at=acquired_at,
            payload=payload,
            representation=ArtifactRepresentation.EXACT_PROVIDER_ARTIFACT,
        )
