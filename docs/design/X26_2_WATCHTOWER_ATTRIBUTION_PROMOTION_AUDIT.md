# X26.2 — Audit: WATCHTOWER Attribution Promotion

Status: INVESTIGATION ONLY. No code changed. Every finding below is traced
through the actual source and confirmed by a live, reproduced example
against `database/wt_ops_v2.db`.

**Verdict, stated up front: the suspicion is confirmed.** Discovery's
"WATCHTOWER ATTRIBUTION" timeline card is rendered for a genuine, reproduced
launch (`EjxEK9QNKUjWSSuB13SgWgWoEZHZ9WJJcwJWBsMBpump`) whose highest
confirmed conclusion is `LINEAGE_GAP` terminating at an `INFRASTRUCTURE`
boundary, with `matched_treasury=NULL`, `reviewed_status='AUTO'`, no
canonical operator, and no Operation Identity. The card is promoted solely
because a row exists in `watchtower_token_attribution` — and that row was
written solely because the walk found a wallet already present in
`wt_discovered_subprovs`, **not** because any treasury was confirmed.

---

## Complete trace: database row → rendered card

**Step 1 — the write.** `src/core/walkback_worker.py`, `_mark_complete()`,
line 422:
```python
if confirmed_subprov or treasury:
    ...
    INSERT INTO watchtower_token_attribution (mint, creator, matched_subprov, matched_treasury, score, tier, scored_at)
    VALUES (?,?,?,?,80,'WALKBACK',?)
```
The gate is **`confirmed_subprov OR treasury`** — an OR, not an AND.
`confirmed_subprov=True` is passed by the walk whenever a hop lands on a
wallet already present in `wt_discovered_subprovs` (verified at the call
site, `walkback_worker.py:634`: `_mark_complete(ops, mint, "LINEAGE_GAP",
subprov, hop1, rpc[0], confirmed_subprov=True)`) — **this fires even when
the outcome is `LINEAGE_GAP` and `treasury` is `None`.** A row is written
to `watchtower_token_attribution` with `matched_treasury=NULL`,
hardcoded `tier='WALKBACK'`, `score=80`.

**Step 2 — the read.** `src/discovery/service.py`, `_entity()`, line 261:
```python
attrib = self._one(conn, tables, "watchtower_token_attribution", "mint = ?", (token,))
```
Line 287:
```python
if attrib:
    a_state = "REJECTED" if attrib.get("reviewed_status") == "REJECTED" else (
        "CONFIRMED" if attrib.get("reviewed_status") == "CONFIRMED" or attrib.get("matched_treasury") else "PROVISIONAL"
    )
    timeline.append(self._node(
        kind="WATCHTOWER_ATTRIBUTION", state=a_state, ...
    ))
```
**The gate that decides whether the card is created at all is `if attrib:`
— truthiness of the row, nothing else.** `matched_treasury` and
`reviewed_status` only affect the card's *state* label (`CONFIRMED` vs.
`PROVISIONAL` vs. `REJECTED`), never whether the card is rendered in the
first place.

**Step 3 — the title.** `templates/discovery.html`, line 234 (Raw
provenance) and line 629 (Attribution chain chip):
```js
esc(String(n.kind||'Evidence').replace(/_/g,' '))
```
`kind="WATCHTOWER_ATTRIBUTION"` → literal string replace of `_` with
space → **"WATCHTOWER ATTRIBUTION"**. There is no conditional wording here
at all — whatever `kind` the backend sets is displayed verbatim (formatted).

## Reproduced live example

| Field | Value |
|---|---|
| Mint | `EjxEK9QNKUjWSSuB13SgWgWoEZHZ9WJJcwJWBsMBpump` |
| `wt_walkback_queue.intelligence_outcome` | `LINEAGE_GAP` |
| `wt_walkback_queue.treasury` | `NULL` |
| `wt_attribution_outcomes.outcome_type` | `LINEAGE_GAP` |
| `wt_attribution_outcomes.terminal_entity_type` | `INFRASTRUCTURE` |
| `watchtower_token_attribution.matched_treasury` | `NULL` |
| `watchtower_token_attribution.matched_subprov` | `56Y1VgppQZFGQCNUXfgyniheVE6we7SDQ2e2MeyA2bik` |
| `watchtower_token_attribution.tier` | `WALKBACK` |
| `watchtower_token_attribution.reviewed_status` | `AUTO` |
| Discovery `a_state` computed | `PROVISIONAL` |
| Discovery card rendered | **"WATCHTOWER ATTRIBUTION"**, state PROVISIONAL |

This is not a hypothetical — this exact mint exists in the live database
today with exactly this combination of facts: no confirmed treasury, no
canonical operator (not checked, but `matched_treasury=NULL` already rules
out the only path to `canonical_identity()`'s gate), infrastructure-only
terminal outcome, and a rendered WATCHTOWER ATTRIBUTION card.

**Every database table involved:** `wt_walkback_queue` (source of
`intelligence_outcome`, `treasury`), `wt_discovered_subprovs` (what
`confirmed_subprov=True` actually checks against), `watchtower_token_attribution`
(the written/read attribution row), `wt_attribution_outcomes` (the separate,
correctly-scoped terminal-outcome table used for cross-reference in this
audit, not itself part of the defect), `wt_confirmed_treasuries` (what
`matched_treasury` should, but in this case does not, reference).

## Decision tree — how "WATCHTOWER ATTRIBUTION" is actually reached

```
Walkback worker processes a FULL_WALKBACK/PARTIAL_* row
  │
  ├─ Hop lands on a wallet in wt_discovered_subprovs
  │    → confirmed_subprov=True passed to _mark_complete()
  │
  │    ├─ That subprov ALSO has a treasury on file
  │    │    → outcome=WATCHTOWER_CONFIRMED, treasury=<real treasury>
  │    │    → watchtower_token_attribution row written (matched_treasury SET)
  │    │    → Discovery: a_state=CONFIRMED, title="WATCHTOWER ATTRIBUTION"
  │    │      (this path is legitimate — a confirmed treasury genuinely exists)
  │    │
  │    └─ That subprov has NO treasury on file
  │         → outcome=LINEAGE_GAP, treasury=None
  │         → confirmed_subprov=True is STILL passed
  │         → gate "if confirmed_subprov or treasury:" STILL PASSES (OR, not AND)
  │         → watchtower_token_attribution row written anyway,
  │           matched_treasury=NULL, tier='WALKBACK', score=80 (hardcoded)
  │         → Discovery: attrib is truthy → card created regardless
  │         → a_state computed as PROVISIONAL (matched_treasury is falsy,
  │           reviewed_status='AUTO' not 'CONFIRMED')
  │         → title STILL renders "WATCHTOWER ATTRIBUTION"
  │           ← THE DEFECT: title is asserted identically in both branches;
  │             only the state badge differs.
  │
  └─ Hop lands on an unrecognised wallet (not in wt_discovered_subprovs at all)
       → confirmed_subprov=False, treasury=None
       → gate fails → no watchtower_token_attribution row written
       → no card rendered at all
```

## Is the promotion condition actually proving WATCHTOWER?

**No.** Tracing exactly what `confirmed_subprov=True` proves: it proves
only that a hop landed on a wallet already present in
`wt_discovered_subprovs` — a table populated by the platform's own
sub-provisioner *discovery* heuristics (per prior memory:
`wt_discovered_subprovs` entries can originate from
`WALKBACK_RECURRING_FUNDER` and similar low-confidence discovery sources,
and X24.9's own investigation found this exact table can contain
false-positive entries like Axiom/Raydium-authority addresses that were
never genuine provisioning wallets at all). Being present in this
discovery table is **not** equivalent to:
- Canonical Operator = WATCHTOWER — never checked anywhere in this code path.
- Operation Identity resolved — never checked; Operation Identity (X25.4) is
  a wholly separate, downstream resolver over `wt_confirmed_treasuries`,
  never consulted here.
- Confirmed treasury — explicitly **not** required by the `OR` gate; the
  reproduced example has `treasury=NULL`.
- `WATCHTOWER_CONFIRMED` outcome — explicitly **not** required; the
  reproduced example has `intelligence_outcome='LINEAGE_GAP'`.
- Canonical operator reached — never checked; `wt_attribution_outcomes`
  for the same mint shows `LINEAGE_GAP`/`INFRASTRUCTURE`, not
  `CANONICAL_OPERATOR_REACHED`.

**The promotion is currently gated solely on: a wallet exists in
`wt_discovered_subprovs`** (via the `confirmed_subprov` flag), **OR** a
treasury was separately confirmed. Since discovery-table membership alone
is sufficient, and discovery-table membership is a much weaker,
provisional signal than treasury confirmation, the card's unconditional
title overstates what a `PROVISIONAL`/no-treasury row actually proves.

## Is the title semantically correct?

**No, not for the `PROVISIONAL`, no-treasury branch.** The title
"WATCHTOWER ATTRIBUTION" is asserted identically regardless of whether
`matched_treasury` is populated — the same word choice is used for a
genuinely confirmed treasury match and for a bare sub-provisioner-discovery
hit with no treasury at all. This is the same class of defect X25.5.1 fixed
for Detection Provenance and X24.8 fixed for the walkback terminal node:
a confirmed-membership-sounding label applied to a case the backend itself
does not confirm.

The title *is* arguably correct for the `CONFIRMED` branch (a real
`matched_treasury` exists) — that case legitimately reflects platform
attribution to a confirmed treasury, consistent with X25.6's
already-established "Platform attribution" framing (the `detector`/`rule`
strings were already corrected there; only the `kind`-derived title itself
was never revisited).

## Smallest logic change required (identified, not implemented)

Two independent, minimal options, from smallest to largest:

1. **Presentation-only fix (smallest, no backend change):** in
   `templates/discovery.html`, branch the rendered title on `n.state`
   instead of unconditionally formatting `n.kind` — e.g. render
   "Platform Attribution (Provisional)" or an equivalent
   confidence-qualified label when `state !== 'CONFIRMED'`, reserving
   "WATCHTOWER ATTRIBUTION"-equivalent wording for the state that actually
   has a `matched_treasury`. This mirrors the same pattern X25.7 already
   used for Detection Provenance (different wording per evidence-completeness
   level) and requires no backend or database change at all.

2. **Backend fix (small, single-condition change):** in
   `src/discovery/service.py`, change the card-creation gate from `if
   attrib:` to `if attrib and attrib.get("matched_treasury"):` (or
   equivalently, only create the node when `a_state` would be `CONFIRMED`),
   so the card is never created at all for a bare sub-provisioner-discovery
   hit with no treasury. This is a one-line, purely additive-restriction
   change to a read-only Discovery function — it does not touch
   `walkback_worker.py`'s writing logic, `watchtower_token_attribution`'s
   schema, or any classification value.

Both are minimal; option 2 more fully resolves the defect (removes the
misleading card entirely rather than re-labeling it), while option 1 is
more conservative (keeps the evidence visible, just honestly labeled). No
implementation was made in this sprint, per its explicit investigation-only
scope.

## Explicit confirmation

No code was changed to produce this document. All queries were read-only
`SELECT`s against `database/wt_ops_v2.db`; `walkback_worker.py`,
`discovery.html`, and `service.py` were read, not modified. `git diff
--stat` for this sprint shows only this new document under `docs/design/`.
