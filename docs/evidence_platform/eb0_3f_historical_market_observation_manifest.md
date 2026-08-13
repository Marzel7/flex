# EB0.3F immutable supplemental market-observation manifest

EB0.3F builds a pure deterministic manifest by replaying a frozen exact GMGN
envelope through EB0.3E, EB0.3C, and EB0.3A. It performs no file writes,
provider calls, database or service access, ranking, scoring, attribution,
policy, or activation.

Each manifest binds exact contract, adapter, normalizer, and schema versions;
platform mint; `1m` request window; run/physical-request/cost accounting; raw
envelope, credential-free projection, and observation-set digests; an exact
two-file hash set; deterministic observations; row, quality, completeness,
conflict, market-cap-presence and earliest-semantics counts; and one manifest
digest. Input ordering is canonical and exact replay is required.

Missing, extra, malformed, or altered source hashes and any changed envelope,
metadata, version, observation, or digest fail closed. The upstream exact-shape
normalizer rejects credentials, cursor/pagination, derived market cap/liquidity,
ranking, scoring, creator/operator attribution and policy content. All facts
remain separate, supplemental, `PARTIAL_INTERVAL`, and
`PAGE_EARLIEST_NOT_HISTORY`; no provider-history completeness or activation
claim is introduced.
