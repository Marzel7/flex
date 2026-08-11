# X78.38A — Overlap-Backed History Continuation

## Result: shadow verifier implemented; production proof blocked

The verifier implements the only safe candidate protocol available from the
current exclusive-`before` request shape: request the next page using the
previous page's penultimate signature, then require the previous oldest
signature to appear exactly once as the first continuation result. If both
pages expose slots, those must match too.

It treats reordering, duplicate overlap, slot mismatch, an absent overlap and
an empty continuation as non-contiguous. It does not issue RPC, advance the
coverage ledger, or change the extractor.

The mechanics are tested, but the required provider semantics experiment and
representative frozen provider-page corpus have not been collected. Therefore
there is no exact full-scan versus shadow-reconstruction comparison, and
X78.38B is skipped rather than enabled.
