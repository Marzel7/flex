# EB0.3E exact GMGN market-kline normalizer

EB0.3E normalizes only the credential-free GMGN v1 shape qualified by the
single-request EB0.3D run: an exact top-level `list`, containing exact candle
fields `time`, `open`, `close`, `high`, `low`, `volume`, `source`, and `amount`.
It has no provider client, URL, credential, database, service, ranking, scoring,
attribution, policy, or activation path.

Caller-supplied immutable metadata binds the platform mint, provider/endpoint
versions, `1m` millisecond window, observation time, run ID, physical sequence,
cost, and observed request count. Exactly one physical request with cost weight
two, no retry/failover/pagination, at most 1,000 rows, and at most 1 MiB is
accepted. Schema or accounting drift fails closed.

`time` maps to `time_ms`; OHLCV decimal strings are retained. Provider-only
`source` and base-token `amount` are validated and explicitly discarded. The
result is replayed through EB0.3C, which fixes authority to supplemental,
completeness to `PARTIAL_INTERVAL`, earliest semantics to
`PAGE_EARLIEST_NOT_HISTORY`, and market cap to unobserved. Qualification uses
the frozen credential-free EB0.3D shape only and makes no broader history,
coverage, pagination, rate-limit, market-cap, liquidity, or activation claim.
