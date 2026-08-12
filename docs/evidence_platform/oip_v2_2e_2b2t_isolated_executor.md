# OIP v2.2E.2B2T isolated executor qualification

`src.acquisition.b2n_qualification` is a local-only primitive. It imports no
provider client or production state. A caller supplies a frozen 20-member,
marked manifest, an injected single-call client, and an append-only JSONL
ledger. It permits exactly one client call per ordinal, records the physical
attempt, and stops immediately after the first non-success or incomplete
evidence result. It is not wired into any production queue or service.
