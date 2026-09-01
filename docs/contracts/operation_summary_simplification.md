# Operation Summary Simplification Contract

The confirmed-operation Summary is a read-only operational view. It never
mutates operator identity, evidence, queues, candidates, or provider state.

## Infrastructure anchors

`operation_summary.build_operation_summary` returns generic anchors with
`role`, `address`, `state`, `qualification`, observation counts, linked-launch
counts, observation bounds, and signature/source provenance. Roles include
`INITIAL_FEE_PAYER` and `TREASURY`; rotation is represented by retaining all
anchors and selecting the most-observed as primary. `CONFIRMED_ANCHOR` requires
at least two retained P3R observations or a confirmed `TREASURY` entity.

## EC1 binding

EC1 is bound from the retained 135-row P3R CSV by its behavioural-profile mint
membership. Its primary initial fee payer is
`DuTbZR8VJGsyLvkhcAyiByPwnPRJj1GTmB88ShgAezCX`, supported by 10 retained
signed traces. Other observed fee payers remain historical anchor records.

## WATCHTOWER compatibility

WATCHTOWER's `operator_entities` rows with `entity_type=TREASURY` render via
the same anchor model, with `role=TREASURY`; no P3R terminology is imposed.

## Summary read/UI contract

The Summary exposes activity state and bounded cadence metrics, one primary
anchor, a qualified behavioural fingerprint, at most three recent launches,
and material changes only when present. Forensic rows remain in Evidence,
Members, Analysis, and History. Behavioural variants do not replace an anchor
or operator identity; infrastructure rotation does not create a new operation.

## Evidence and safety

Anchors carry source/signature provenance. The model does not perform RPC,
delete evidence, promote candidates, replay queues, or emit trading signals.
