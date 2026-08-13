# EB0.3A supplemental historical market observation contract

EB0.3A is a pure deterministic projection over already bounded frozen records.
It performs no provider, network, database, service, ranking, scoring, identity,
or policy work. Every fact is bound to a platform-owned mint and is permanently
classified `SUPPLEMENTAL_NON_AUTHORITATIVE`.

Facts preserve provider and endpoint versions, candle interval and UTC bounds,
observation time, OHLCV, optional market cap, quote unit, quality, completeness,
conflict group, source digest, physical request sequence/cost, and content-derived
provenance and identity digests. Decimal values are canonical strings and OHLC
ranges fail closed when incoherent. Conflicting provider facts remain separate.

Earliest-observation semantics are explicit and deliberately narrow:
`NOT_ASSERTED`, `PROVIDER_WINDOW_EARLIEST`, or `PAGE_EARLIEST_NOT_HISTORY`.
None asserts chain birth, global market-history completeness, or birth valuation.
Provider evidence cannot define creator identity, migration, canonical birth or
birth market cap, rankings, scores, profitability/cashflow, operator identity,
or policy. Provider integration, request execution, live compatibility, corpus
assembly, and activation remain separate human-gated milestones.
