# EB0.1C deterministic source adapters

EB0.1C adds pure, frozen-fixture-qualified adapters into the EB0.1A contract. The adapter version is `eb0.1c.v1`; generated `source_version` values combine that version, the adapter kind, and the upstream schema/version, using the explicit suffix `legacy-unversioned` when the committed legacy source has no version field.

## Qualified mappings

- A verified `EvidenceRecord` `LaunchFact` maps to `CHAIN_BIRTH` using `creation_timestamp`. Its raw-artifact digest is preserved. It never supplies a valuation.
- An explicit `receive_utc_ns` record maps to `PLATFORM_FIRST_SEEN`. Generic `created_at` or mutable `analyzed_at` inputs fail closed.
- An explicit migration receive boundary maps to `MIGRATION` as an observed event. A legacy `migrated_at` proxy without that boundary fails closed rather than being called exact chain time.
- An explicit first price or market-cap row maps to `MARKET_FIRST_OBSERVED`. Separate rows remain separate facts, including conflicting same-boundary values.

All adapters retain `source_record_digest` in the projected observation and EB0.1A provenance identity. Missing valuation is always `null` with `UNKNOWN/NOT_OBSERVED`; no adapter emits `BIRTH_MARKET_CAP`.

The module imports no runtime, database, queue, service, network, or provider code. Qualification uses frozen fixtures only and does not authorize materialization from production state or activation.
