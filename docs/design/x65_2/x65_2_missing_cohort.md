# X65.2 — Phase 1: Reproduce the Missing-Evidence Cohort

Read-only reproduction, 2026-07-21, `7d` window, against the live
production database. Reuses the exact filter from X65.1 plus
`src/ops/treasury_resolution.py`'s live resolution status for each launch.

## Confirmation against X65.1

| Check | Expected | Actual | Match |
|---|---|---|---|
| Total cohort | 19 | 19 | ✅ |
| Resolved (`KNOWN_TREASURY`) | 7 | 7 | ✅ |
| Unresolved | 12 | 12 | ✅ |

Exact reproduction — no drift since X65.1's own measurement earlier
today, confirming this cohort's population and resolution split remain
stable enough to investigate without needing to re-derive anything.

## Full cohort record

| Mint | Creator | CREATE (UTC) | Migration (UTC) | Canonical behaviour | Creator identity | Topology | Operation | Treasury status |
|---|---|---|---|---|---|---|---|---|
| GuyE9St1cU54ppHwqD719Q2AHf6AmPha93MEjzv2pump | G22uhsudCS1gVx... | 2026-07-15T12:20:35Z | 2026-07-15T12:20:36Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none (resolved via treasury walkback) | KNOWN_TREASURY |
| B3Fq8SqBtsxsWw5wqCL5wnJr3pgGYTrTVEvwSMXipump | D8bfGDnHgJfPj3... | 2026-07-15T14:48:09Z | 2026-07-15T14:48:10Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| CmoCuZ9J2YT1QHv28p3QRphhZot6Sdbu6P6Aw4Vmpump | EEJh8HhcH6zVu1... | 2026-07-17T11:26:39Z | 2026-07-17T11:26:41Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| HHcXBLbnuSWdYigNgiYDmPhuwwRzTCB73CmyJ8M7pump | 7nxHcmxbaM4FC2... | 2026-07-20T13:33:22Z | 2026-07-20T13:33:23Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 2GuvMWJpfNBXdZQZVGEWLV1Dx8qfiLKHHoDDfe4Apump | 3NyJNH93vBDM7n... | 2026-07-18T12:20:15Z | 2026-07-18T12:20:20Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| EQZfBpWpQc5BEUsP3q79xk1k3mKAAeL8bVZ5m1LJpump | FPLauDPp7DqMCj... | 2026-07-20T00:38:08Z | 2026-07-20T00:38:15Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 2XmV6Jk6ATzKCnVB15cnPHCCF9o4Kn4PXvVFk6Rppump | Dsm6w4zFsovcGT... | 2026-07-16T17:03:13Z | 2026-07-16T17:03:14Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| DpTtRHY6PSuxxJEjdd2NGW22F5JgP8WmWYBK48jhpump | GZeJHhQSm4S87K... | 2026-07-18T04:44:06Z | 2026-07-18T04:44:07Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump | 71ftvekAkhanTd... | 2026-07-20T14:45:28Z | 2026-07-20T14:45:29Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 3QFvseNX1Fdkc6SZV4AT2BfSDvMUH4xQDY1H7TbPpump | 2zEEWsBtLFfkJW... | 2026-07-16T12:36:27Z | 2026-07-16T12:36:28Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 3LZL5cXac86U1ti81V8GEA1qoj3HenLfnJMcQo7opump | 96oi3HjrPWGnkP... | 2026-07-16T10:45:35Z | 2026-07-16T10:45:53Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 4WfoYERYFw3AQWc3MiJz4H8YScu7sbGFoSX7xCMepump | GAJ5JACjNXeeTX... | 2026-07-17T18:20:09Z | 2026-07-17T18:20:10Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| EDNvjVDjKVfRsqxf3C8nN2sunxctfoboE2S8aUHGpump | HAsNHBL5Bex4g8... | 2026-07-18T10:41:30Z | 2026-07-18T10:41:31Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| 71TKvknpvwRcjdoYPngxw6895yeidY24nY8eJnHCpump | AuTE4s6LMnyXrH... | 2026-07-16T15:59:33Z | 2026-07-16T15:59:34Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| c5Zye8yFd1AGrSJ2mViYgXWa1kgCdCj5RWhen6tpump | A2EFKGqAoM1pFF... | 2026-07-17T22:55:25Z | 2026-07-17T22:55:27Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| x8NtU6nnYDn1BwMDGg2oFdBuYBevhJ32kqM97FSpump | FWWz8PHebMuo77... | 2026-07-15T20:52:52Z | 2026-07-15T20:52:53Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |
| 9Mn2t7yX2TmSSMEsQqDnFvcmNAGVCPhjevXpKfqgpump | 7d3RkvUGJ8u5Jn... | 2026-07-21T12:14:43Z | 2026-07-21T12:14:45Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| FzNgpR11RYACasA8ptFniXQKcLw26CmBWdyNEAU1pump | J6TN4WtDZL5ig3... | 2026-07-17T10:05:47Z | 2026-07-17T10:05:48Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | UNRESOLVED |
| HJ1Ry6iJyAqN7jozMTErJHuNA66kpkDkowi7fhCRpump | 42yXX31Xdx3d9U... | 2026-07-15T09:48:59Z | 2026-07-15T09:49:00Z | QUICK_BIRTH_MIGRATION | FRESH_CREATOR | UNKNOWN | none | KNOWN_TREASURY |

`funding origin` is `UNKNOWN` for all 19, uniformly (implied by
`topology='UNKNOWN'`, per X65.1's Phase 1 finding that these two are
equivalent for this specific population). `operation` is
`__UNASSIGNED__` for all 19 (the cohort's own defining filter).

## Split for this investigation

- **7 KNOWN_TREASURY launches** are out of scope for X65.2's remediation
  focus (they already have working attribution via a cross-reference
  join, per X65.1) — included in this table for completeness only.
- **12 UNRESOLVED launches** are this investigation's actual subject:
  `B3Fq8SqBtsxsWw...`, `CmoCuZ9J2YT1QH...`, `HHcXBLbnuSWdYi...`,
  `EQZfBpWpQc5BEU...`, `DpTtRHY6PSuxxJ...`, `CvP9vVUCpoDuMd...`,
  `4WfoYERYFw3AQW...`, `EDNvjVDjKVfRsq...`, `71TKvknpvwRcjd...`,
  `c5Zye8yFd1AGrS...`, `9Mn2t7yX2TmSSM...`, `FzNgpR11RYACas...`.

## Timing note

All 12 unresolved launches span **2026-07-15T14:48 through
2026-07-21T12:14** — i.e., the entire 7-day window, not clustered at
either the oldest or newest edge. This is an early signal against a
simple "too recent for the indexer to catch up yet" explanation (which
would predict clustering near 2026-07-21) — the oldest unresolved
launch (`B3Fq8SqBtsxsWw...`, 2026-07-15) is nearly 6 days old at the
time of this investigation, ample time for any normal-cadence indexing
pass to have processed it if the pipeline were otherwise healthy.
