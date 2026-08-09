# OIP v2.2C.1 — Canonical Primitive Replay Divergence Audit

## Verdicts

- **Replay contract:** D — CONTRACT AMBIGUOUS / MUST BE FORMALIZED
- **Divergence:** D — MULTIPLE CAUSES
- **Next step:** FORMALIZE_REPLAY_CONTRACT_FIRST
- **OIP v2.2C:** Do not resume until the replay contract is formalized.

## Executive finding

All 54,320 canonical-only Primitive observations are explained. They own 5,940,717 provenance links, exactly the complete canonical-versus-clean provenance surplus. There are zero clean-only Primitive IDs, zero missing supporting Evidence IDs, and no shared-Primitive provenance difference: Primitive identity includes its complete ordered Evidence set, so a shared ID necessarily has the same provenance inputs.

The divergence has two measured causes:

| Classification | Observations | Percentage |
|---|---:|---:|
| OLDER_PRIMITIVE_VERSION | 39,694 | 73.07% |
| SUPERSEDED_DERIVED_STATE | 14,626 | 26.93% |
| Unresolved | 0 | 0% |

No row was deleted or changed.

## Exact population

| Family | Canonical-only | Links | Current subject equivalent | Discovery/motif support | Direct relationship support |
|---|---:|---:|---:|---:|---:|
| WALLET_FRESH_AT_EVENT | 39,694 | 79,459 | 39,694 | 0 | 0 |
| BEHAVIOURAL_TIMING | 14,298 | 5,859,348 | 14,298 | 0 | 0 |
| REPEATED_COUNTERPARTY | 328 | 1,910 | 328 | 328 | 0 |
| **Total** | **54,320** | **5,940,717** | **54,320** | **328** | **0** |

The 328 recurrence observations affect candidate/motif inputs across 206 subjects. No canonical-only Primitive ID occurs directly in the persisted relationship report. Indirect relationship changes remain possible because removing a supporting Primitive changes candidate and motif identities; no removal was simulated or authorized.

## Insertion and version evidence

Primitive storage records `primitive_version`, `generated_at`, quality, and Evidence IDs. It does not record `inserted_at`, run ID, producer commit, parser version, or milestone. `generated_at` is therefore the best available era marker, not a proven insertion timestamp.

Every surplus row says Primitive version `1`. Git history shows only three relevant implementation commits: EP2.0 introduced all three generators; EP2.1 changed freshness parameters and temporal evaluation; v2.1A later optimized indexing without changing intended output. Recurrence and timing contracts have no later semantic implementation change.

EP2.1 changed `WALLET_FRESH_AT_EVENT` while retaining version `1`, despite the EP2 contract saying logic changes require a new version. The pre-fix EP3 report contains exactly 39,694 freshness rows, exactly matching the full canonical-only freshness population. The old and corrected identities coexist because their parameter sets differ.

## WALLET_FRESH_AT_EVENT

The current generator evaluates each native `BalanceFact` at its immutable transaction boundary. It combines the balance, matching `AddressHistoryObservation`, and matching `AccountParticipationFact`; provider history is newest-first and only entries after the reference transaction are predecessors.

EP2.1 corrected the earlier implementation, added `history_order=NEWEST_FIRST` and `reference_boundary=STRICTLY_PRECEDING`, and changed missing-reference behavior. It did not increment `primitive_version`. Append-only storage retained the old identities and later inserted corrected identities. All 39,694 old observations have a current same-subject equivalent and surviving Evidence.

Classification: **OLDER_PRIMITIVE_VERSION** in substance, with an incorrectly unchanged stored version label.

## REPEATED_COUNTERPARTY

The generator groups all `DIRECT_COUNTERPARTY` observations by source/destination and emits one aggregate after two distinct signatures. Identity includes the growing Evidence set and output transaction count/window.

A deterministic fixture proves that an incremental two-event aggregate and a final three-event clean aggregate have different IDs. Append-only persistence retains the earlier aggregate each time the corpus grows. The 328 surplus observations occur across multiple later stage timestamps; all have a same-subject clean equivalent.

Classification: **SUPERSEDED_DERIVED_STATE**.

## BEHAVIOURAL_TIMING

The generator groups all timestamped Primitives by subject, orders them by chain timestamp/Primitive ID, and emits the complete event-type sequence, deltas, sample count, window, and unioned Evidence set. Any new event or changed upstream Primitive changes the aggregate identity.

A deterministic fixture proves that incremental two-event timing and final three-event timing generate different IDs. Canonical-only timing spans every acquisition stage, all 14,298 rows have same-subject clean equivalents, and their 5,859,348 links are dominated by `AccountParticipationFact` (5,291,950 links). Both growing cohorts and the unversioned freshness correction can change timing identities.

Classification: **SUPERSEDED_DERIVED_STATE**.

## Evidence survival and matrix

All supporting Evidence survives in the frozen 807,545-row Evidence population. This is established by the canonical foreign-key contract and v2.2B's successful resolution of all 12,398,192 provenance pairs through the complete Evidence identity map.

Major canonical-only link contributions:

- BEHAVIOURAL_TIMING → AccountParticipationFact: 5,291,950
- BEHAVIOURAL_TIMING → BalanceFact: 229,867
- BEHAVIOURAL_TIMING → TransactionFact: 229,670
- WALLET_FRESH_AT_EVENT → BalanceFact: 39,694
- WALLET_FRESH_AT_EVENT → AccountParticipationFact: 39,694
- REPEATED_COUNTERPARTY → NativeMovementFact: 1,309
- REPEATED_COUNTERPARTY → TokenMovementFact: 601

The complete matrix is persisted in the machine-readable summary.

## Replay contract

The existing contract contains conflicting signals:

- storage is immutable and append-only;
- different Primitive versions may coexist;
- replaying identical Evidence should reproduce the same ID and insert no duplicate;
- logic changes require a new version;
- aggregate identities include the complete retained Evidence cohort;
- no contract defines whether earlier aggregate snapshots remain authoritative after the cohort grows.

This does not cleanly specify current-state rebuild or historical derived ledger semantics. The observed table behaves as a hybrid ledger, but the family-specific authority rules were never formally defined, and EP2.1 violated the documented version-change rule. Selecting HYBRID now would bless accidental accumulation as architecture. The defensible verdict is therefore **contract ambiguous**.

## Required follow-up

Formalize, per Primitive family:

1. whether authority is current-state or historical snapshot;
2. whether snapshot boundary/run identity belongs in the contract;
3. mandatory version increments for semantic changes;
4. which population Discovery and later layers consume;
5. reconciliation and rollback rules for already accumulated version-`1` rows.

Only then can the programme choose between preserving the accumulated canonical relation during compact migration or reconciling to clean current-state replay in a separate shadow-proven repair. Compact storage remains relationally valid and unchanged. Acquisition remains held.

## Artifacts

- `database/evidence_platform/oip_v2_2c1_divergence_audit/canonical_only_classification.jsonl.gz`
- `database/evidence_platform/oip_v2_2c1_divergence_audit/oip_v2_2c1_summary.json`
- `database/evidence_platform/oip_v2_2c1_divergence_audit/analysis.sqlite`
- `database/evidence_platform/oip_v2_2c1_divergence_audit/relationship_scan.json`

Zero RPC, acquisition, production interaction, canonical deletion, provenance deletion, semantic change, or compact-storage change occurred.
