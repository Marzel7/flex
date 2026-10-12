# DEV033 Phase A — chronological evidence preparation

**Status:** READY FOR SEPARATE ACQUISITION AUTHORIZATION.  This is a DEV-only,
provider-free planning artifact.  No credential was read and no provider, RPC,
database, queue, runtime, or production action occurred.

## Frozen pilot

The sampling frame is the 539 durable DEV020 comparable outcomes.  The requestable
population is **448**: 81 records are excluded because their opening baseline is
incompatible or non-strict for an opening-entry simulation, and 10 lack a retained
post-peak window.  This is a disclosed eligibility filter, not an outcome-based
selection.

`docs/audits/dev033_chronological_backtest_pilot_manifest_20261013.v1.json`
contains the exact 30 records, source opening/peak facts, post-peak drawdown,
four retrospective strata, fixed windows, and request identities.  Its selection
uses deterministic SHA-256 mint tie-breaks and marginally balances peak-multiple,
time-to-peak, post-peak-drawdown, and launch-date terciles.  Crash-event outcomes
are not an input.  Marginal counts are 10/10/10 for time-to-peak, drawdown, and
launch date; peak-multiple is 9/10/11 due the deterministic 30-record balance.
Canonical content hash:
`a2c4202338d58651c20462405990d02b40f44bb210b0efe68ecf6c99d7961885`.
File SHA-256:
`f5a8a2289bb369b95b44896fc6d7f7db3bc79c890a3bba1b0fcd3ec32f3662d3`.

## Existing evidence and acquisition plan

The 539 retained DEV014 lifecycle journals hold compact extrema/coverage only
(`OBSERVED_RESPONSE_ONLY`); they do **not** retain ordered OHLC candles.  DEV028
and DEV031 retain ordered 30-second observations only for their narrow crash
windows and cannot support a representative strategy backtest.  New chronological
evidence is therefore required.

Every frozen pilot request is one fixed Birdeye `/defi/v3/ohlcv` 1-minute MCAP
range: from floor(opening timestamp / 60) to the earlier of retained lifecycle
end and observed-peak + 7,200 seconds, rounded up to a minute.  The largest window
is below the existing 60,000-second / 1,000-candle response bound, so the manifest
has exactly **30 requests** and does not require pagination or adaptive splitting.
The total planned observation capacity is 5,230 one-minute buckets.

Phase B must use only these identities: serial execution, six-second pacing,
real `DevProviderBudget`, at most ten requests/minute, one 30-token session
(within the 50-token ceiling), and no retry, pagination, fallback, RPC, or 30-second
substitution.  Any 30-second follow-up to resolve a specific candle ambiguity needs
separate authorization and its own manifest.

## Retention and backtest feasibility

Retain compact normalized candle rows only: timestamp, interval, MCAP open/high/low/
close, gap markers, request identity/window, provenance, and coverage classification.
Reject raw payload retention.  Bound each token artifact to 256 KiB, the aggregate
session evidence to 8 MiB, and any journal/temporary file to 4 MiB; expected compact
ordered-candle content is approximately 1.2 MiB for 5,230 rows.  These bounds are
well below the 500 MB prohibition and have no unbounded writer.

The resulting 1-minute evidence can support a provider-free backtest for strategies
with a maximum 120-minute horizon.  It must process candles chronologically; use a
running peak only; classify high/low same-candle target/stop collisions as
ambiguous/conservative; never assume an exact threshold fill; and report gaps,
untriggered entries, and missing exits separately.

No liquidity, executable swap price, fee, slippage, or failed-execution evidence is
retained.  Phase B/analysis may run sensitivity scenarios, but cannot claim realized
net SOL profitability.  The minimum reliable ordering evidence is complete ordered
OHLC intervals covering each entry through its exit horizon plus explicit gap flags;
liquidity/execution evidence remains a separate required layer for executable claims.

## Decision

Phase A is complete.  The only next step is a fresh, separately authorized Phase B
execution of this exact 30-request manifest.  It is not authorized by this artifact.
