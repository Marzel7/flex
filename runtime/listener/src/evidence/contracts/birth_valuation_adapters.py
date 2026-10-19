"""Pure EB0.1C adapters into the EB0.1A birth/valuation contract."""

from __future__ import annotations

from typing import Mapping


ADAPTER_VERSION = "eb0.1c.v1"


class BirthValuationSourceAdapterError(ValueError):
    """Named fail-closed error for ambiguous or malformed source evidence."""


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BirthValuationSourceAdapterError(f"EB0_1C_INVALID_{field.upper()}")
    return value.strip()


def _integer(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BirthValuationSourceAdapterError(f"EB0_1C_INVALID_{field.upper()}")
    return value


def _seconds_to_ns(value: int) -> int:
    return value * 1_000_000_000


def _version(kind: str, upstream: str | None = None) -> str:
    suffix = upstream.strip() if isinstance(upstream, str) and upstream.strip() else "legacy-unversioned"
    return f"{ADAPTER_VERSION}:{kind}:{suffix}"


def _missing_value_base(
    *, mint: str, event_kind: str, event_time_ns: int, source: str,
    source_version: str, observed_at_ns: int, source_record_digest: str,
    quality_state: str,
) -> dict[str, object]:
    return {
        "mint": mint,
        "event_kind": event_kind,
        "event_time_utc_ns": event_time_ns,
        "source": source,
        "source_version": source_version,
        "observed_at_utc_ns": observed_at_ns,
        "price_or_market_cap_value": None,
        "valuation_semantics": "UNKNOWN",
        "quality_state": quality_state,
        "completeness_state": "NOT_OBSERVED",
        "source_record_digest": source_record_digest,
    }


def adapt_launch_fact(record: Mapping[str, object]) -> dict[str, object]:
    """Adapt a verified committed EvidenceRecord LaunchFact to CHAIN_BIRTH."""

    if record.get("fact_family") != "LaunchFact":
        raise BirthValuationSourceAdapterError("EB0_1C_NOT_LAUNCH_FACT")
    if record.get("verification_state") != "VERIFIED":
        raise BirthValuationSourceAdapterError("EB0_1C_LAUNCH_NOT_VERIFIED")
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise BirthValuationSourceAdapterError("EB0_1C_INVALID_LAUNCH_PAYLOAD")
    event_seconds = _integer(payload, "creation_timestamp")
    acquired_seconds = _integer(record, "acquired_at")
    _text(payload, "creation_signature")
    _integer(payload, "creation_slot")
    _text(payload, "program_id")
    _text(payload, "source_platform")
    if acquired_seconds < event_seconds:
        raise BirthValuationSourceAdapterError("EB0_1C_LAUNCH_ACQUIRED_BEFORE_EVENT")
    return _missing_value_base(
        mint=_text(payload, "mint"),
        event_kind="CHAIN_BIRTH",
        event_time_ns=_seconds_to_ns(event_seconds),
        source=f"evidence_launch:{_text(record, 'source_id')}",
        source_version=_version("launch-fact", record.get("source_version")),
        observed_at_ns=_seconds_to_ns(acquired_seconds),
        source_record_digest=_text(record, "raw_artifact_digest"),
        quality_state="VERIFIED",
    )


def adapt_platform_receive(record: Mapping[str, object]) -> dict[str, object]:
    """Adapt an explicit receive-boundary record; generic created_at is forbidden."""

    if "created_at" in record or "analyzed_at" in record:
        raise BirthValuationSourceAdapterError("EB0_1C_AMBIGUOUS_PLATFORM_TIMESTAMP")
    receive_ns = _integer(record, "receive_utc_ns")
    return _missing_value_base(
        mint=_text(record, "mint"),
        event_kind="PLATFORM_FIRST_SEEN",
        event_time_ns=receive_ns,
        source=_text(record, "source"),
        source_version=_version("platform-receive", record.get("source_schema_version")),
        observed_at_ns=receive_ns,
        source_record_digest=_text(record, "source_record_digest"),
        quality_state="OBSERVED",
    )


def adapt_observed_migration(record: Mapping[str, object]) -> dict[str, object]:
    """Adapt a migration receive boundary without claiming exact chain time."""

    if "migrated_at" in record and "receive_utc_ns" not in record:
        raise BirthValuationSourceAdapterError("EB0_1C_AMBIGUOUS_MIGRATION_TIMESTAMP")
    receive_ns = _integer(record, "receive_utc_ns")
    _text(record, "signature")
    return _missing_value_base(
        mint=_text(record, "mint"),
        event_kind="MIGRATION",
        event_time_ns=receive_ns,
        source=_text(record, "source"),
        source_version=_version("migration-receive", record.get("source_schema_version")),
        observed_at_ns=receive_ns,
        source_record_digest=_text(record, "source_record_digest"),
        quality_state="OBSERVED",
    )


def adapt_market_observation(record: Mapping[str, object]) -> dict[str, object]:
    """Adapt one explicit first price or market-cap observation."""

    captured_at = _integer(record, "captured_at")
    observed_at = _integer(record, "observed_at")
    if observed_at < captured_at:
        raise BirthValuationSourceAdapterError("EB0_1C_MARKET_OBSERVED_BEFORE_CAPTURE")
    value_kind = _text(record, "value_kind")
    if value_kind == "MARKET_CAP":
        value = _text(record, "value")
        semantics = "MARKET_CAP_AT_EVENT"
    elif value_kind == "PRICE":
        value = _text(record, "value")
        semantics = "PRICE_AT_EVENT"
    else:
        raise BirthValuationSourceAdapterError("EB0_1C_UNKNOWN_MARKET_VALUE_KIND")
    quality = "CONFLICTING" if record.get("conflicting") is True else "OBSERVED"
    return {
        "mint": _text(record, "mint"),
        "event_kind": "MARKET_FIRST_OBSERVED",
        "event_time_utc_ns": _seconds_to_ns(captured_at),
        "source": _text(record, "source"),
        "source_version": _version("market-observation", record.get("source_schema_version")),
        "observed_at_utc_ns": _seconds_to_ns(observed_at),
        "price_or_market_cap_value": value,
        "valuation_semantics": semantics,
        "quality_state": quality,
        "completeness_state": "COMPLETE",
        "source_record_digest": _text(record, "source_record_digest"),
    }
