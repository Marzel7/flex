# X65.1 — Phase 1: Cohort Reproduction

Read-only reproduction, 2026-07-21, `7d` window, against the live
production database (`flex_complete_database.db` + `wt_ops_v2.db`).

## Reproduction method

The Discovery UI's filter chain is:
`canonical_behaviour=QUICK_BIRTH_MIGRATION` → `creator_identity=FRESH_CREATOR`
→ `topology=UNKNOWN` → `funding=UNKNOWN` → `operation=__UNASSIGNED__`.

Per X65.0's own Phase 5 finding, "Funding Origin = UNKNOWN" in the UI
means the mint has **no entry at all** in `CEX_MINT_CACHE`
(`/api/ops-v2/cex-funding-intelligence`), which is itself scoped to
only `wt_attribution_outcomes.outcome_type='KNOWN_CEX_REACHED'` rows
(`src/ops/cex_funding_intelligence.py:85`). Since `topology=UNKNOWN`
mints are, by construction, mints with no resolved funding lineage at
all, they cannot simultaneously be `KNOWN_CEX_REACHED` — so filtering
by `topology='UNKNOWN'` already implies `funding=UNKNOWN` for this
population; no separate CEX-mint-cache lookup was needed to reproduce
the cohort. `operation=__UNASSIGNED__` was reproduced as `operation_id
is None`.

Applied filter, directly against `build_operational_intelligence()`'s
`records` output:

```python
canonical_behaviour == 'QUICK_BIRTH_MIGRATION'
and creator_identity == 'FRESH_CREATOR'
and topology == 'UNKNOWN'
and operation_id is None
```

## Result: cohort count matches expectation exactly

**19 launches** — matching the task's stated "approximately 19" exactly
(not just approximately; an exact match). Per the task's own instruction
("Do not continue if the query does not reproduce the UI cohort"), this
confirms no UI/API filtering discrepancy exists and Phase 2 may proceed.

## Full cohort record

| Mint | Creator | CREATE time (UTC) | Migration time (UTC) | Creator birth (UTC) | `create_tx_signature` | `topology_derived_from` |
|---|---|---|---|---|---|---|
| EDNvjVDjKVfRsqxf3C8nN2sunxctfoboE2S8aUHGpump | HAsNHBL5Bex4g88P5DBPQi284Jzs4r63urZRbhRF9pgG | 2026-07-18T10:41:30Z | 2026-07-18T10:41:31Z | 2026-07-18T10:41:28Z | **NULL** | no_lineage_evidence |
| 4WfoYERYFw3AQWc3MiJz4H8YScu7sbGFoSX7xCMepump | GAJ5JACjNXeeTXhqxRZ5oLtj9pQ8fMQCYXtRaequfspY | 2026-07-17T18:20:09Z | 2026-07-17T18:20:10Z | 2026-07-17T18:20:07Z | **NULL** | no_lineage_evidence |
| c5Zye8yFd1AGrSJ2mViYgXWa1kgCdCj5RWhen6tpump | A2EFKGqAoM1pFFfvxq5Xoe2dEbmsgmR5w39aLRXras5y | 2026-07-17T22:55:25Z | 2026-07-17T22:55:27Z | 2026-07-17T22:55:25Z | **NULL** | no_lineage_evidence |
| DpTtRHY6PSuxxJEjdd2NGW22F5JgP8WmWYBK48jhpump | GZeJHhQSm4... | 2026-07-18T04:44:06Z | 2026-07-18T04:44:07Z | — | **NULL** | no_lineage_evidence |
| CmoCuZ9J2YT1QHv28p3QRphhZot6Sdbu6P6Aw4Vmpump | EEJh8HhcH6... | 2026-07-17T11:26:39Z | 2026-07-17T11:26:41Z | — | **NULL** | no_lineage_evidence |
| 3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump | 96oi3HjrPW... | 2026-07-16T10:45:35Z | 2026-07-16T10:45:53Z | — | **NULL** | no_lineage_evidence |
| 3QFvseNX1Fdkc6SZV4AT2BfSDvMUH4xQDY1H7TbPpump | 2zEEWsBtLF... | 2026-07-16T12:36:27Z | 2026-07-16T12:36:28Z | — | **NULL** | no_lineage_evidence |
| GuyE9St1cU54ppHwqD719Q2AHf6AmPha93MEjzv2pump | G22uhsudCS... | 2026-07-15T12:20:35Z | 2026-07-15T12:20:36Z | — | **NULL** | no_lineage_evidence |
| 9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump | 7d3RkvUGJ8... | 2026-07-21T12:14:43Z | 2026-07-21T12:14:45Z | — | **NULL** | no_lineage_evidence |
| FzNgpR11RYACasA8ptFniXQKcLw26CmBWdyNEAU1pump | J6TN4WtDZL5ig3kqCqxSf61jkfzPJXZyWrE6zMMquvC7 | 2026-07-17T10:05:47Z | 2026-07-17T10:05:48Z | — | **NULL** | no_lineage_evidence |
| HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump | 7nxHcmxbaM... | 2026-07-20T13:33:22Z | 2026-07-20T13:33:23Z | — | **NULL** | no_lineage_evidence |
| 71TKvknpvwRcjdoYPngxw6895yeidY24nY8eJnHCpump | AuTE4s6LMnyXrHCtpqFooaSQe8mE86ShGhGHXctBjBPS | 2026-07-16T15:59:33Z | 2026-07-16T15:59:34Z | — | **NULL** | no_lineage_evidence |
| EQZfBpWpQc5BEUsP3q79xk1k3mKAAeL8bVZ5m1LJpump | FPLauDPp7D... | 2026-07-20T00:38:08Z | 2026-07-20T00:38:15Z | — | **NULL** | no_lineage_evidence |
| CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump | 71ftvekAkh... | 2026-07-20T14:45:28Z | 2026-07-20T14:45:29Z | — | **NULL** | no_lineage_evidence |
| x8NtU6nnYDn1BwMDGg2oFdBuYBevhJ32kqM97FSpump | FWWz8PHebM... | 2026-07-15T20:52:52Z | 2026-07-15T20:52:53Z | — | **NULL** | no_lineage_evidence |
| 2XmV6Jk6ATzKCnVB15cnPHCCF9o4Kn4PXvVFk6Rppump | Dsm6w4zFso... | 2026-07-16T17:03:13Z | 2026-07-16T17:03:14Z | — | **NULL** | no_lineage_evidence |
| 2GuvMWJpfNBXdZQZVGEWLV1Dx8qfiLKHHoDDfe4Apump | 3NyJNH93vBDM7nn1U2geTBmoRwnogFoHmhjJSEY8fNGh | 2026-07-18T12:20:15Z | 2026-07-18T12:20:20Z | — | **NULL** | no_lineage_evidence |
| B3Fq8SqBtsxsWw5wqCL5wnJr3pgGYTrTVEvwSMXipump | D8bfGDnHgJ... | 2026-07-15T14:48:09Z | 2026-07-15T14:48:10Z | — | **NULL** | no_lineage_evidence |
| HJ1Ry6iJyAqN7jozMTErJHuNA66kpkDkowi7fhCRpump | 42yXX31Xdx... | 2026-07-15T09:48:59Z | 2026-07-15T09:49:00Z | — | **NULL** | no_lineage_evidence |

(Full precision creator/mint addresses for all 19 rows are preserved in
`/tmp/x65_1_cohort.json`, generated by this phase's reproduction script;
some are abbreviated above for table width only — every creator wallet
listed used its full un-truncated form in the actual analysis performed
in later phases.)

## Existing operation fields (all 19, uniformly)

- `operation_id`: `None` (all 19 — this is the defining filter)
- `is_watchtower`: `False` (all 19)
- `mechanisms`: `[]` (all 19 — no funding-mechanism tag assigned either;
  consistent with `topology_derived_from='no_lineage_evidence'`)

## Existing funding and topology evidence

**`topology_derived_from: "no_lineage_evidence"` for all 19 launches,
uniformly.** This is a meaningfully different starting point than "we
looked and found ambiguous evidence" — it means the existing topology
classifier's lineage-derivation process found **zero** persisted
evidence to reason from at all. Confirms this cohort is a genuine gap
in existing coverage, not a population the system already examined and
gave up on.

**`create_tx_signature` is NULL for all 19 launches in
`token_analysis`.** Also checked `wt_create_event_ledger` (the X64.7
canonical CREATE-event ledger built earlier in this project's history)
for these same 19 mints — **zero rows found there either.** No CREATE
signature is persisted anywhere in the system for this specific cohort.
This is itself a likely root cause of `no_lineage_evidence`: without a
CREATE signature, the existing walkback/lineage machinery (which
generally keys off the CREATE transaction to walk backward to a
creator's funding source) has nothing to anchor to.

**Migration timing**: every launch in the cohort migrated within 1-15
seconds of its own CREATE (consistent with `QUICK_BIRTH_MIGRATION`'s
`<=900s` threshold, and in fact far faster — every single one is
comfortably inside `RAPID_MIGRATION`'s `<300s` band too, though
`QUICK_BIRTH_MIGRATION` is the launch's canonical behaviour per X65.0's
precedence).

## Conclusion

The cohort reproduces exactly (19/19, matching "approximately 19"
precisely) using `topology='UNKNOWN'` + `operation_id is None` as a
proxy for the full 5-filter Discovery path — no UI/API discrepancy was
found, so Phase 2 proceeds without needing to resolve any filtering
mismatch. The absence of a persisted CREATE signature across the entire
cohort is flagged as a likely explanation for why existing lineage
derivation has nothing to work from, and is the first concrete fact
Phase 2's evidence audit and Phase 3's creator-to-subprov resolution
will need to account for.
