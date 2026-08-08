# EP3.2B — Bounded 3SW2 Freshness Evidence Completion

## Outcome

All four missing activation references were recovered through the existing
Shared Transaction Acquisition layer. Immutable acquisition observations,
artifacts, and `AddressHistoryObservation` Evidence were appended to the
isolated 3SW2 shadow corpus. No transaction fetches or production writes were
performed.

## Four-creator census and result

| Creator | Activation reference | Initial state | Pages recovered | Final state |
|---|---|---:|---:|---:|
| `8cTQRGkwEzAf4TyCz2bb1qHZvkPpugp7htMzjQzEbWwE` | `wQunDwx8DZmXxboSWiocpHjWdARARKa7yFvxTdn1CAeTB3rmUXPGxHYxUvuQ7Z5N2GXxvWiqJQ8CKWZZNjgwsf3` | UNKNOWN / UNVERIFIABLE | 3 | VERIFIED_FRESH |
| `97VLEFqD5rPusWSiX7ujEWtwatkZAfDAappVGgv6xsXq` | `5uRjev2Jb3YDek7Ci2LZPNJqYH3fm6ABfGwpuXHKWEkRmnbY8gr15KC4tUq3Wor8DTByAVkBftqDAxtDKAoHTZGP` | UNKNOWN / UNVERIFIABLE | 10 | VERIFIED_FRESH |
| `DGRbGNSrUxKBQX684dCRUbcfgnDduZTodBUKhLH52qf7` | `5P6w3RhSCZy4CVrByRGdzhyYpAZwBXkMR8iFjPZ7PdPSxSNXxT4SAAWMaPGRvvKxiREFYM1BN7FH13dorm1Pz9Pf` | UNKNOWN / UNVERIFIABLE | 3 | VERIFIED_FRESH |
| `EtBgBxufWn5hiFHK2FaNJxCXjhmKPdP2WUYbYd3bqsX2` | `3oWvHy5YZi7W1fc9HZXy3yXjkMFsTi7TFv6Pnt76386qguw5ZQYgVfUJUCgjNv68YmrPwY1cAK59j5uFQiez3Cjw` | UNKNOWN / UNVERIFIABLE | 3 | VERIFIED_FRESH |

Each initial observation contained 1,000 newer signatures and omitted the
activation reference. The required observation was exactly one bounded
address-history page containing that immutable reference.

## Budget and acquisition

- Expected minimum: 4 RPC calls / 40 credits
- Hard ceiling: 40 RPC calls / 400 credits
- Actual provider calls: 20
- Actual credits: 200
- Successful Evidence pages appended: 19
- Transaction fetches: 0
- Population expansion: 0
- Provider: Solana public RPC through Shared Transaction Acquisition
- Duration: 23.495 seconds

One additional provider call is transport accounting; 19 unique successful
responses became acquisition observations and normalized Evidence.

## Replay and parity

- Corpus envelopes: 59
- Two independent incremental replays: identical
- Replay RPC: 0
- 3SW2 creators verified fresh: **13 / 13**
- Unknown/unverifiable creators: **0 / 13**
- Primitive counts: unchanged
- Unrelated primitive changes: 0
- WATCHTOWER activation inputs changed: **0 / 24**

EP3.2 now reports:

`PARITY_COMPLETE_WITH_CLASSIFIED_LEGACY_LIMITATIONS`

The remaining classified differences are the already documented generic
Primitive v1 limits for legacy exclusion identities and governed non-membership;
there are no unexplained freshness differences.

## Safety

- Evidence schema changes: 0
- Primitive Engine changes: 0
- Runtime changes: 0
- Operation Contract changes: 0
- Governance actions: 0
- Production database writes: 0
- Production consumers: 0

