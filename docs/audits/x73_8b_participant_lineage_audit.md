# X73.8B Participant Lineage Audit

## 3hJX evidence census

The canonical membership contains 64 selected descendant mints. Read-only census before implementation found:

| Evidence | Member launches covered | Distinct identities |
| --- | ---: | ---: |
| `token_analysis` | 64 | 62 creators |
| Migrated token records | 64 | — |
| `wt_walkback_queue.creator` | 64 | 62 creators |
| `wt_walkback_queue.subprov` | 64 | 64 provisioning clients |
| `wt_walkback_queue.treasury` | 0 | 0 |
| `wt_provisioning_edges` | 64 | 66 source / 123 destination wallets across all associated edges |
| Selected `candidate_parent` with persisted role `OPERATIONAL_TREASURY` | 64 | 1 treasury: 3hJX |

The authoritative `token_analysis` creator and persisted walkback creator agree for all 64 mints. Sixty-one of the 64 walkback subproviders also appear as the direct `from_wallet` on a source-mint provisioning edge; all 64 subproviders are explicitly persisted on their launch-keyed walkback rows.

## Root cause

X73.8A added selected descendant mints to infrastructure-only population membership after the legacy seed-only participant enrichment had already run. The adapter therefore received 64 launch identities but empty creator and treasury sets, while `client_wallets` fell back to the single population member wallet. No participant evidence was missing; the participant hydration path did not include infrastructure-only membership.

## Canonical projection

For infrastructure-only populations:

1. `SELECTED wt_walkback_edge_candidates` establishes mint membership.
2. Those exact mints join to `wt_walkback_queue`.
3. `creator` supplies distinct observed creators.
4. `subprov` supplies distinct **Provisioning clients**.
5. A selected `candidate_parent` whose persisted candidate role is `OPERATIONAL_TREASURY` supplies the directly evidenced treasury identity; a queue treasury is also accepted when present.
6. The immutable Investigation Population unions these identity sets and the Registry/Profile consume the same projected arrays.

Alternative descendants do not contribute launches or participants. Existing outcome- and provisioning-routed populations retain their established participant sources and are not broadened.
