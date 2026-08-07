"""EP1.3B logical-fact and immutable-observation identity contract."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence


def _validate_json(value: Any, path: str = "$", *, allow_null: bool = True) -> None:
    if value is None:
        if not allow_null:
            raise TypeError(f"{path} may not be null")
        return
    if isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        raise TypeError(f"{path} uses float; immutable Evidence requires integer units")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string object key")
            _validate_json(item, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_json(item, f"{path}[{index}]")
        return
    raise TypeError(f"{path} contains unsupported type {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON: exact strings, UTF-8, sorted keys, compact separators.

    Arrays retain source order. Null is JSON ``null``. Integers use JSON base-10
    formatting. Floats, bytes, sets and non-string object keys are prohibited.
    No Unicode normalization is applied because it could change observed data.
    """
    _validate_json(value)
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nonempty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _digest(name: str, value: str) -> str:
    _nonempty(name, value)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def payload_digest(payload: Mapping[str, Any]) -> str:
    return _sha256(canonical_json_bytes(dict(payload)))


def logical_fact_id(
    *, fact_family: str, chain: str, network: str, natural_key: str
) -> str:
    """Provider/parser-independent identity of the underlying occurrence."""
    identity = [
        _nonempty("fact_family", fact_family),
        _nonempty("chain", chain),
        _nonempty("network", network),
        _nonempty("natural_key", natural_key),
    ]
    return _sha256(canonical_json_bytes(identity))


def evidence_id(
    *,
    fact_family: str,
    fact_schema_version: str,
    logical_fact_id_value: str,
    parser_id: str,
    parser_version: str,
    normalized_payload_digest: str,
    raw_artifact_digest: str,
) -> str:
    """Identity of one immutable artifact/parser-scoped observation."""
    identity = [
        _nonempty("fact_family", fact_family),
        _nonempty("fact_schema_version", fact_schema_version),
        _digest("logical_fact_id", logical_fact_id_value),
        _nonempty("parser_id", parser_id),
        _nonempty("parser_version", parser_version),
        _digest("normalized_payload_digest", normalized_payload_digest),
        _digest("raw_artifact_digest", raw_artifact_digest),
    ]
    return _sha256(canonical_json_bytes(identity))
