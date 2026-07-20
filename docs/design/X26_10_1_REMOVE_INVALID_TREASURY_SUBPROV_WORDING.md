# X26.10.1 — Remove Invalid Treasury/Sub-Provisioner Wording from Terminal Infrastructure Behaviour

Status: Implemented, tested, live-verified against the real Binance/CEX
and Axiom launches. No detection, walkback, attribution, operation
identity, or schema logic changed — a narrow presentation-layer
correction confined to `src/ops/operational_behaviour.py`.

---

## Phase 1 — Trace of the invalid lines

Reproduced live via the real Discovery path
(`B7E5xQ66FkG9uj3QjAoaDxK2SkVcFqA3wCA9eWa1pump`, terminal entity Binance 2):

| Line | Builder | Input field | Source table.column | Role before fix | Real treasury? | Real sub-provisioner? | Observation or role assignment? |
|---|---|---|---|---|---|---|---|
| `Creator funding observed via PLAIN_XFER` | `_build_behaviour_summary` | `edges["subprov_to_creator"].funding_mechanism` | `wt_provisioning_edges` (`edge_type='SUBPROV_TO_CREATOR'`) | Already correctly role-neutral (X26.8) | N/A | No | Observation — correct as-is |
| `Treasury funded sub-provisioner via PLAIN_XFER` | `_build_behaviour_summary` | `edges["treasury_to_subprov"].funding_mechanism` | `wt_provisioning_edges` (`edge_type='TREASURY_TO_SUBPROV'`) | **Unconditional — no role check at all** | **No** — confirmed live, the "treasury" address (`3ADzk5YDP9sgorvPSs9YPxigJiSqhgddpwHwwPwmEFib`) is just a wallet that sent ~59,000 SOL to Binance; nothing independently establishes it as a treasury | **No** — Binance's `wt_discovered_subprovs` row is `state=REJECTED_INFRASTRUCTURE` | **Role assignment** — the bug. Asserts a treasury/sub-provisioner relationship that was never established, purely from an edge's historical shape |
| `Historical funding relationship recorded (provisioning session exists; funder is not a valid sub-provisioner)` | `_build_behaviour_summary` | `session is not None` | `wt_provisioning_sessions` row-exists check | Already correctly role-neutral (X26.8), but exposed internal implementation detail in the wording itself | N/A | N/A | Observation, but worded around an internal condition rather than an analyst fact |

**Confirmed**: the `TREASURY_TO_SUBPROV`-shaped edge is genuinely
operation-agnostic historical pipeline metadata for this terminal-
infrastructure case — it records that *some* wallet sent funds to Binance
via `PLAIN_XFER` at some point, tagged with a role-shaped `edge_type`
purely because of which table/column the historical walkback pipeline
happened to write it into, not because a real treasury role was ever
independently confirmed.

## Phase 2 — Role-aware relationship wording rule

**Governing rule implemented**: the `"Treasury funded sub-provisioner via
{mechanism}"` line is now gated on `funder_role == VALID_SUBPROVISIONER`
— the exact same gate every other role-specific line in this file already
used. For any other role (`REJECTED_INFRASTRUCTURE`, `OTHER_REJECTED`,
`UNRESOLVED_FUNDER`), the line is **omitted entirely** rather than
reworded, per Phase 5's dedup guidance — the creator-funding line
immediately above it already communicates the one effective observed
relationship (same mechanism, same terminal address) without a second,
unearned label describing the same transfer twice.

Valid provisioning paths are completely unaffected — when
`funder_role == VALID_SUBPROVISIONER` (both a genuine treasury and a
genuine, non-rejected sub-provisioner are independently established),
`"Treasury funded sub-provisioner via {mechanism}"` still renders exactly
as before. Verified live against a real `PROVISIONAL_SUBPROV` wallet with
a genuine two-hop chain — unchanged.

## Phase 3 — Entity-aware edge interpretation

Confirmed the underlying principle directly: `wt_provisioning_edges
.edge_type` (`TREASURY_TO_SUBPROV`/`SUBPROV_TO_CREATOR`) is written by
the historical walkback/session pipeline based on *which columns the
funding relationship happened to populate* — not based on any current
canonical entity classification. The fix treats `edge_type` purely as
"this table/column contained the funding record," never as authority
over role wording; `funder_role` (from `_resolve_funder_role()`, itself
based on `wt_discovered_subprovs.state` and the reviewed infrastructure
registry) is the only source of truth for whether role-specific language
is justified. This was already true for the `SUBPROV_TO_CREATOR` edge's
wording (X26.8) — Phase 3's audit found the `TREASURY_TO_SUBPROV` edge
was the one place this principle wasn't yet applied.

## Phase 4 — Historical relationship wording cleaned up

```
Before: "Historical funding relationship recorded (provisioning session
         exists; funder is not a valid sub-provisioner)"
After:  "Funding relationship reconstructed from historical chain data"
```

No internal table/column names (`wt_provisioning_sessions`), no
implementation conditions (row existence), and no restatement of "funder
is not a valid sub-provisioner" — the rest of the card (the role-neutral
funding line and the "Funding source: ... reviewed ..." line) already
establishes this without needing to say it again.

## Phase 5 — Duplicate observation prevented

Verified live and via test (`test_duplicate_mechanism_wording_consolidated`):
exactly **one** `PLAIN_XFER`-mechanism statement now appears in Behaviour
Summary for a terminal-infrastructure funder — `"Creator funding observed
via PLAIN_XFER"` — not two. The final summary for Binance now reads
exactly as the brief's preferred model:
```
Creator funding observed via PLAIN_XFER
Funding source: Binance 2 · reviewed exchange
Launches attributed here: 156
Distinct creators observed: 115
Funding relationship reconstructed from historical chain data
```

## Phase 6 — Cross-type consistency

Verified live/via test across all reviewed subtypes (CEX, automation,
bridge, relay, custody): none render `"Treasury funded sub-provisioner"`
or `"Sub-provisioner funded creator"`; all follow the identical neutral
relationship model, differing only in the `"Funding source: {name} ·
reviewed {subtype}"` phrase (unchanged from X26.10):

| Wallet | Subtype phrase |
|---|---|
| Axiom | `reviewed automation infrastructure` |
| Binance 2 | `reviewed exchange` |
| deBridge Bridge Vault | `reviewed bridge` |
| Relay.link Solver | `reviewed relay` |
| Fireblocks Custody | `reviewed custody infrastructure` |

## Phase 7 — Genuine provisioning behaviour preserved

Verified live against a real `PROVISIONAL_SUBPROV` wallet
(`BWwpES2oYug1SsLKPyFXekdJK99dHtdPgBjNk1SPRMDu`) with a genuine
`WSOL_WRAP_CLOSE` two-hop treasury→subprov→creator chain:
```
Sub-provisioner funded creator via WSOL_WRAP_CLOSE
Treasury funded sub-provisioner via WSOL_WRAP_CLOSE
Sub-provisioner has funded 22 creators
Walkback completed successfully (provisioning session recorded)
```
Identical to pre-fix output — the fix did not flatten genuine provisioning
structure. Also verified `SEEDED_ACCOUNT_CLOSE`-mechanism paths and a
sub-provisioner with no edges at all (creator-count-only rendering) —
both unaffected.

## Phase 8 — Tests

`tests/test_x26_10_1_remove_invalid_treasury_subprov_wording.py` — 18
tests, all passing:
- `test_cex_never_renders_treasury_funded_subprovisioner`,
  `test_sub_provisioner_funded_creator_never_appears_for_terminal_infra`.
- `test_all_reviewed_types_follow_same_neutral_wording` (parametrized
  Axiom/bridge/relay/custody).
- `test_plain_xfer_remains_visible`, `test_funding_source_label_remains_visible`,
  `test_attributed_launch_and_creator_metrics_unchanged`.
- `test_historical_wording_has_no_implementation_detail`,
  `test_no_internal_table_or_column_names_in_prose`.
- `test_duplicate_mechanism_wording_consolidated` — asserts exactly one
  `PLAIN_XFER` line.
- `test_genuine_treasury_subprov_creator_chain_retains_role_wording`,
  `test_genuine_seeded_account_close_path_retains_role_wording`,
  `test_genuine_subprov_without_edges_still_shows_creator_count`.
- `test_x26_10_subtype_labels_unchanged`.
- `test_discovery_e2e_no_side_effects` — full `DiscoveryService.resolve()`
  round-trip confirming `attribution_outcome`/`canonical_identity`/
  `operation_identity` untouched and no DB mutation.
- `test_no_database_mutation`.

**Full regression**: 120/120 passing across this new suite plus
`test_x26_10_unified_terminal_infrastructure.py`,
`test_x26_9_1_infrastructure_activity_metrics.py`,
`test_x26_8_reject_state_aware_operational_behaviour.py`,
`test_x26_7_evidence_presentation_refresh.py`,
`test_x26_6_1_reject_state_aware_provenance.py`,
`test_discovery_workspace.py`, `test_x26_2_1_attribution_gate_fix.py`,
`test_ops_x20_6_discovery_prioritisation.py`, and the pre-existing
`test_ops_x21e_operational_behaviour_rendering.py`. No existing test
assertions needed updating — none of them exercised a fixture with both
`t_to_s` populated and a non-valid role together.

## Live verification

Restarted `watchtower_api`, fetched two real mints:

- **`B7E5xQ66FkG9uj3QjAoaDxK2SkVcFqA3wCA9eWa1pump`** (the exact Binance
  mint that reproduced the original defect) now returns:
  ```
  Creator funding observed via PLAIN_XFER
  Funding source: Binance 2 · reviewed exchange
  Launches attributed here: 156
  Distinct creators observed: 115
  Funding relationship reconstructed from historical chain data
  ```
  No `"Treasury funded sub-provisioner"`, no `"Sub-provisioner funded
  creator"`, no `"provisioning session exists"`, no `"funder is not a
  valid sub-provisioner"`. `attribution_outcome.outcome_type
  =KNOWN_CEX_REACHED`; `canonical_identity: null`; `operation_identity:
  null` — unaffected.
- **`2GTswvgFNGucLwrUMvttVshy28C5bmjgsuQZ4eVcpump`** (Axiom) follows the
  identical model.
- **`BWwpES2oYug1SsLKPyFXekdJK99dHtdPgBjNk1SPRMDu`** (genuine
  `PROVISIONAL_SUBPROV`) retains its exact original role-specific wording.
- `git status --porcelain -- database/*.db` empty — no DB mutation.

## Confirmation that only presentation logic changed

- Only `src/ops/operational_behaviour.py` was modified — a single
  conditional gate added to one line (`t_to_s`'s treasury-wording), and
  one line's wording changed (the historical-session line).
- No file under `src/core/` (detection), `src/ops/attribution_outcome.py`,
  `src/discovery/service.py`, walkback, or operation identity was
  touched.
- No schema migration, no new column, no new table.
- `wt_provisioning_edges`, `wt_provisioning_sessions`, and all other raw
  evidence tables are read-only in this module (unchanged from prior
  sprints) — nothing in this fix writes to or deletes from any table.
