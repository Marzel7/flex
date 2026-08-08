# EP3.2 — 3SW2 Operation Contract v1

3SW2 is implemented as an independent, shadow-only Operation Contract. It uses
the unchanged generic runtime but does not reuse WATCHTOWER behaviour or topology.

The local topology is strictly `Controller → Creator → Launch`. A controller edge
requires a proven `SYSTEM_TRANSFER` whose source equals the controller declared by
the contract. A creator-to-launch edge requires a proven `LAUNCH_SIGNER`. No
treasury or intermediate provisioner is inferred.

Direct counterparty observations are reported as operational contact with an
identity effect of `NONE`. The detector emits no confidence score, canonical
identity claim, lifecycle transition, or executable governance action.

## Parity status

The X78.21 comparison baseline contains one canonical controller and 13 historical
launches. The locally available immutable shadow corpus contains none of those 13
launches. Consequently all required parity dimensions are classified as **Missing
Evidence**, with zero unexplained differences. No corpus expansion or RPC was
performed.

Run the deterministic report with:

```bash
python scripts/validate_three_sw2_contract_v1.py
```

Synthetic immutable snapshots prove deterministic behaviour, topology, detector
output, contact/identity separation, and coexistence with WATCHTOWER v1. Full
historical parity remains blocked until a separately approved 3SW2 shadow corpus
is materialized.
