# EB0.3G immutable supplemental market-observation output bundle

EB0.3G writes a frozen EB0.3F result only to a caller-supplied new or empty
directory. The exact file set is `run.json`, `projection.json`, `manifest.json`,
`observations.json`, and `hashes.json`. All JSON is canonical; writes use
exclusive creation, and existing content is never overwritten.

The bundle binds the caller-supplied engineering revision, request metadata,
credential-free EB0.3E projection, EB0.3F manifest, deterministic EB0.3A
observations, per-file hashes, and one bundle digest. Verification rejects
missing, extra, altered, re-encoded, or internally inconsistent files and
replays the supplied frozen envelope through the entire EB0.3E/C/A/F stack.

The bundle accepts no credential, pagination, inferred market cap/liquidity,
ranking, scoring, attribution, profitability/cashflow, or policy content. It
does not fetch, publish, deploy, activate, or access production/runtime state.
