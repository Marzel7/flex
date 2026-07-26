# X64.8 Regression Results

Focused X64.8 and Discovery regression run: **73 passed**.

Covered:

- Fresh, Single-use, Repeat, Returning, Dormant/Reactivated and Unknown classification
- mutually exclusive precedence
- Disposable Creator scoring and missing-evidence behavior
- indexed batched enrichment
- zero RPC/network path
- Behaviour -> Creator Identity -> Topology gating
- downstream Funding and Attribution preservation
- stale URL/reset behavior
- 1,000-creator performance guard

An additional broad run exposed five pre-existing failures in `test_ops_x21e_operational_behaviour_service.py` caused by its fixture omitting a `state` column expected by that separate service. X64.8 does not call or modify that service; the failures are recorded rather than hidden.

The broader Discovery-named suite produced **108 passed, 2 failed**. Both failures are stale X20.6 copy/URL assertions (`Knowledge changes and analyst actions first` and a literal `window=24h` URL) that predate X64.8; neither exercises creator identity or progressive filtering.
