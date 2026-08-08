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
launches. EP3.2A materialized all 13 inside a frozen shadow corpus: 13 launch
signers, 13 controller activations, 13 economic-funding observations, and all 13
required creator histories. Acquisition used 16 bounded RPC calls in total
(30 credits for missing transaction/signature recovery and 130 credits for the
thirteen required history observations). No population expansion occurred.

Run the deterministic report with:

```bash
python scripts/validate_three_sw2_contract_v1.py
```

Immutable snapshots prove deterministic behaviour, topology, detector output,
contact/identity separation, and coexistence with WATCHTOWER v1. Explicit
exclusions and known non-members remain classified legacy-governance context;
they are not promoted into generic chain primitives. Historical freshness parity
is blocked by a Primitive v1 implementation defect: returned history signatures
are not filtered relative to the reference event, so later activity is treated as
prior activity and historically fresh creators are emitted as `NOT_FRESH`.
