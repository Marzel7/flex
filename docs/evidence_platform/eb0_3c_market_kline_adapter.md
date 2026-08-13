# EB0.3C bounded market-kline response adapter

EB0.3C is a safe-local adapter from one exact, credential-free frozen page
projection into EB0.3A supplemental observations. It contains no GMGN client,
URL, authentication, database, service, ranking, scoring, attribution, policy,
or activation path. An injected frozen/fake transport is invoked exactly once.

The accepted projection binds the platform mint, provider and endpoint
identifiers/versions, `1m` interval, millisecond request bounds, observation
time, run ID, physical request sequence and cost, response digest, bounded OHLCV
rows, quality, completeness, conflict state, and earliest-page semantics. The
schema is exact, candles are ordered and bounded to 1,000 rows, decimal strings
and OHLC ranges are validated, and credential-bearing or analytical-policy
fields fail closed.

Every output remains `SUPPLEMENTAL_NON_AUTHORITATIVE`,
`PARTIAL_INTERVAL`, and `PAGE_EARLIEST_NOT_HISTORY`. Market cap is always
unobserved (`None`): price candles are never converted to market cap. EB0.3C
makes no live GMGN shape, history, pagination, lookback, completeness, rate-limit,
or mint-coverage compatibility claim. Any provider request requires a separate
human-authorized qualification milestone.
