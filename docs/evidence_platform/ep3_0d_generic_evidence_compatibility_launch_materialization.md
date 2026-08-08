# EP3.0D — Generic Evidence Compatibility & Launch Materialization

Status: **COMPLETE — SOFTWARE BLOCKERS RESOLVED**

EP3.0D resolves the two generic compatibility blockers identified by EP3.0C.
It does not implement or execute an Operation, detector, topology, runtime,
identity, or governance path. Validation used only previously cached artifacts;
additional RPC calls and production writes were both zero.

## Provider numeric compatibility

The EP3.0C failure census contained 410 failures and one failure class:

```text
$.parsed_fields.tokenAmount.uiAmount uses float;
immutable Evidence requires integer units
```

Each affected provider object also contains the canonical raw token `amount`
and integer `decimals`. Parser version 2 excludes provider display fields
`uiAmount` and `uiAmountString` from normalized Evidence while preserving their
exact representation in the content-addressed raw artifact. Canonical integer
amount and decimals remain in Evidence. No conversion, rounding, or inferred
integer is performed, so precision is not lost and provider provenance remains
replayable.

The compatibility rule is deliberately narrow. Any float outside these provider
display fields still violates the integer-only Evidence contract and fails
normalization. Provider disagreement remains separate immutable observations;
the compatibility layer does not merge or resolve it.

Result: **4,577 / 4,577 available artifacts normalize successfully**. The 410
numeric failures are reduced to zero without weakening the Evidence contract.

## Operation-neutral launch decoding

The generic decoder recognizes objectively observable Pump `create` and
`create_v2` instruction discriminators and their documented account layouts.
It emits only:

- creation transaction signature and instruction position;
- mint;
- creator account and observed signer state;
- program/platform identifier;
- slot and block time;
- fee payer;
- decoder version and immutable Evidence provenance.

It emits no Operation, treasury, controller, attribution, identity, confidence,
topology, or governance interpretation. Unknown programs and discriminators
remain undecoded rather than inferred.

Result: **171 deterministic LaunchFacts** are generated from the existing raw
artifacts.

## Before / after corpus

| Measure | EP3.0C | EP3.0D |
|---|---:|---:|
| Cached artifacts available | 4,577 | 4,577 |
| Cached artifacts missing | 1,510 | 1,510 |
| Normalization complete | 4,167 | 4,577 |
| Normalization failed | 410 | 0 |
| TransactionFacts | 4,167 | 4,577 |
| LaunchFacts | 0 | 171 |
| Primitive observations | 60,436 | 80,212 |
| Evidence-complete launches | 0 / 176 | 82 / 176 |
| Primitive-complete launches | 0 / 176 | 82 / 176 |
| Runtime-ready launches | 0 / 176 | 82 / 176 |

The Primitive Engine required no code or semantic changes. Existing Primitive
Contract v1 generation naturally produced `LAUNCH_SIGNER` (171),
`LAUNCH_ACTIVATION` (25), and `ECONOMIC_FUNDING` (25) after LaunchFacts became
available.

## Deterministic replay

A clean replay rebuilt Evidence and Primitives from retained raw artifacts only.

| Projection | Semantic digest |
|---|---|
| Evidence | `5dc3073be0a5ab8d2ed551b750f77eff6a1e450dc4eea50b3b000992fc26ebb6` |
| Primitives | `dc5d4118309d04051725a230e86f1d86aab4f15280bda0d90cc38ef5cea849a3` |
| Primitive inputs | `7fae7ba0e566f97e6b340de2a4a4da4a30bf6a159889488883b497adcb0e94fd` |

All expected and actual digests match. Replay made zero RPC calls and created no
duplicate logical facts.

## Remaining readiness constraints

The remaining 94 launches are not runtime-ready because required historical
artifacts are absent or their historical source signatures were not recorded.
The corpus still reports 1,510 unavailable artifact references with an explicit
reason per object. These are data-availability constraints, not normalization,
LaunchFact, Primitive, or runtime defects.

No bounded historical acquisition was attempted. EP3.1 remains gated on the
separate decision about how much historical comparison coverage is required.
