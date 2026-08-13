# EB0.2C deterministic creator-outcome source adapters

EB0.2C adapts only immutable EB0.1 `MintCorpus` evidence and two explicit,
provenance-bound inputs: a qualified `CreatorIdentityFact` and an
`ObservationWindowFact`. It performs no discovery or I/O.

Creator identity accepts only a verified `pf_ws_creator` fact or canonical
CREATE proof. The historically ambiguous `earliest_tx_creator` fallback is
rejected. Cohort boundaries must be an explicit `CHAIN_BIRTH`,
`PLATFORM_FIRST_SEEN`, or `MIGRATION` event; a market observation is never
promoted to birth.

The adapter maps a migration or qualifying market-cap observation to a positive
only when its canonical event time is inside the requested horizon and no later
than the frozen observation cutoff. A market threshold uses the canonical
event-time value, never legacy `market_cap_highest`.

Absence maps to `OBSERVED_FALSE` only when the supplied observation-window fact
explicitly asserts full-horizon completeness and its cutoff reaches the horizon
end. Otherwise absence remains `UNKNOWN/NOT_OBSERVED` and is excluded from the
denominator. Output lineage binds the corpus, manifest, creator fact, window
fact, adapter version, cohort kind, outcome kind, and candidate observation IDs.

Profiles, aggregation, rankings, scores, profitability, operator attribution,
live adapters, GMGN supplements, and activation remain outside this contract.
