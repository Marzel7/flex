# OIP v2.0 — Coverage Expansion & Operational Landscape

This programme-opening slice exposes the validated EP4 landscape through a
read-only, identity-free analyst surface and freezes `OIP_V2_COVERAGE_V1`.

## Coverage baseline

The eligible migrated population is defined deterministically as a launch with
`migration_tx` present or `lifecycle_stage = migrated`. The baseline command is:

`python scripts/measure_oip_v2_coverage.py`

The current Evidence corpus is WATCHTOWER-oriented. It must not be described as
whole-platform coverage. Incremental acquisition may target only eligible rows
with already-known creation or migration signatures until a separately approved
signature-discovery contract exists.

## Analyst surface

`/intelligence/landscape` shows motifs, dominant structures and neighbourhoods.
Motif and neighbourhood explorers expose only immutable measurements. The
checked-in snapshots do not contain occurrence-level Evidence references, so
that drill-down is explicitly `UNAVAILABLE`; the UI never synthesizes it.

## Controlled migration

The first production-consumer migration target is developer-only reconciliation
diagnostics. It will use `off → shadow → serve` dual-read parity, fail open to the
legacy diagnostic response, and require zero unexpected field-level deltas before
serve. Discovery, Operations and governance remain unchanged in this slice.
