# EB0.2D immutable creator historical outcome manifests

EB0.2D seals deterministic EB0.2A facts emitted from frozen EB0.2C evidence.
Each manifest binds schema, contract, and adapter versions; canonical input and
projection digests; the ordered facts; and a manifest digest. Replay rebuilds
the complete manifest and requires exact equality.

The manifest reports fact count, eligible-denominator count, UNKNOWN count,
NOT_OBSERVED count, outcome-kind/state counts, quality/completeness counts, and
conflicting-fact count. It preserves separate facts across creator, mint,
horizon, threshold, and source identity. Exact duplicate inputs fail closed.

These counts disclose evidence coverage; they do not calculate rates, aggregate
creator profiles, rank or score creators, infer profit or cash flow, or attribute
operators. Live sources, providers, GMGN, and activation remain outside EB0.2D.
