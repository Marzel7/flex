# EB0.1E frozen canonical corpus assembly

EB0.1E deterministically assembles canonical EB0.1A/EB0.1C facts into one EB0.1D manifest per mint. Each event kind has its own earliest boundary: `CHAIN_BIRTH`, `PLATFORM_FIRST_SEEN`, `MIGRATION`, and `MARKET_FIRST_OBSERVED` are never compared or collapsed across kinds.

Every fact tied at a kind's earliest timestamp is retained, including conflicting market observations. Later facts are excluded from the canonical manifest but remain explicitly accounted for by observation identity, timestamp, selected boundary, and reason `LATER_THAN_EARLIEST_EVENT_KIND_BOUNDARY`. Missing valuation stays represented in the selected manifest.

Mint ordering, selected observations, exclusions, counts, and corpus digests are input-order independent. Empty inputs and malformed event boundaries fail closed. The assembler has no file, database, service, provider, network, or clock dependency and is qualified only against frozen EB0 fixtures.
