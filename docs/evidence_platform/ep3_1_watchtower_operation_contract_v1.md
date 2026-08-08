# EP3.1 — WATCHTOWER Operation Contract v1

EP3.1 implements WATCHTOWER as the first named Operation Contract executed by the
generic EP3 runtime. It is shadow-only. Legacy WATCHTOWER remains authoritative.

## Contract boundary

- Inputs are immutable runtime Evidence and Primitive windows only.
- The six behaviour modules summarize wrap-close, creator freshness, directional
  funding, launch identity, timing, and repeated counterparties.
- Topology contains only proven `SYSTEM_TRANSFER` edges. It does not infer
  treasury, controller, or canonical identity.
- The detector emits no confidence score, no canonical identity claim, and only
  `NO_AUTOMATIC_GOVERNANCE`.
- Presentation and monitoring are declarative and non-authoritative.

No production database, RPC client, legacy WATCHTOWER module, or governance
executor is imported or called by contract evaluation.

## Shadow parity

The deterministic parity report is generated solely from the frozen local corpus:

```bash
python scripts/validate_watchtower_contract_v1.py
```

The frozen comparison population remains 62 treasuries, 176 launches, 5,937
legacy provisioning edges, and 69 canonical entities. Current immutable primitive
coverage contains `LAUNCH_SIGNER` observations for 136 of the 176 frozen launches.
The other 40 are classified as **Missing Evidence**. Legacy provisioning-edge
identity and campaign grouping are classified as **Known legacy limitation**
because Primitive Contract v1 has no approved primitive that reproduces those
legacy interpretations.

These differences are reported; they are not normalized away and do not expand
Evidence, Primitive, or runtime semantics.

## Replay and versioning

Runtime outputs are content-derived and deterministic. Re-evaluating an identical
snapshot produces identical observation, topology, and detector identities.
The contract is registered as `watchtower@1.0.0` in `SHADOW`, allowing later
versions to coexist through the existing EP3 registry without replacing v1.
