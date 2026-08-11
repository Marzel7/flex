# X78.38A2 — Same-Slot Pagination Proof

## Verdict: `BLOCK_HISTORY_REUSE`

Two identical, anchored Helius continuations for the X78.38A1 counterexample
returned the same membership but a different ordering of signatures sharing the
same Solana slot. The expected multi-signature overlap window was present in
both responses, but its members moved to different positions.

This establishes that Helius provider-local ordering within a slot is unstable.
The enhanced response lacks a transaction index, and a page can cut through a
slot, so neither a signature window nor a complete final-slot set can prove
that every historical transaction between pages has been observed.

No further overlap heuristic is safe under the current acquisition contract.
Incremental history reuse remains disabled.
