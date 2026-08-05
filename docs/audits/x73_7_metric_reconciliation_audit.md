# X73.7 Registry/Profile Metric Reconciliation Audit

## Metric contracts

| Metric | Registry source | Profile source | Rule |
| --- | --- | --- | --- |
| Launches | Reconciled list projection `family.launches` | Detail projection `family.launches` | Same attributed-launch aggregate; labelled **Attributed launches** in profile metadata. |
| Creators | List projection `creator_count`, captured before address arrays are stripped | Detail `unique_creators.length` | Same distinct creator membership where persisted; unavailable is displayed as `—`. |
| Clients | `client_wallets.length` relationship summary | `client_wallets.length` | Same current client membership. |
| Treasuries | `treasuries`/`member_treasuries` relationship summary | `treasuries.length`, falling back to intelligence infrastructure treasuries | Same current treasury membership; unavailable is `—`. |

No filtering or reconciliation logic was changed.

## Launch-record distinction

The profile previously used `intelligence.performance.launches` to decide whether to show an empty launch table. That collection contains persisted per-launch detail rows and is not the attributed-launch total. For 3hJX the reconciled aggregate is 63 attributed launches while detailed performance rows are currently unavailable.

The Members tab now always shows the reconciled attributed total and separately states detailed-record coverage. When detail rows are absent it says: “No launch records are currently available for this Investigation Population.” It no longer implies that the attributed launches do not exist.

## Named consistency snapshot

| Object | Disposition | Attributed launches | Detailed records |
| --- | --- | ---: | ---: |
| WATCHTOWER | Confirmed Operation | 176 | 176 |
| 3SW2 | Confirmed Operation | 13 | 13 |
| B48k / Dv34 Family | Unresolved | 79 | 79 |
| 3hJX Family | Unresolved | 63 | 0 (explicitly explained) |
| C7Ha Family | Review | 7 | 7 |
| 5tzF Family (infrastructure control) | Infrastructure | 190 | 190 |

Snapshot recorded from the local production projection during X73.7 validation. Registry and Profile use the same aggregate for their headline launch number.
