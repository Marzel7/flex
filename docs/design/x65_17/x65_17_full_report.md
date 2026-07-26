# X65.17 — Audit the wt_provisioning_edges Write Path (Full Report)

Read-only source-code audit. No code changes, no database writes, no UI changes.
Every claim in this report is backed by a direct file/line citation — no conclusion
is inferred from database contents (X65.16's approach); this audit proves behavior
from the implementation itself, per the task's explicit requirement.

## Contents

1. [Locate Every Write](#phase-1--locate-every-write)
2. [Trace the Complete Write Path](#phase-2--trace-the-complete-write-path)
3. [Determine Exactly What Creates One Row](#phase-3--determine-exactly-what-creates-one-row)
4. [Creator Filtering](#phase-4--creator-filtering)
5. [Cardinality](#phase-5--cardinality)
6. [Test the Fan-Out Interpretation](#phase-6--test-the-fan-out-interpretation)
7. [Relationship to Raw Blockchain Data](#phase-7--relationship-to-raw-blockchain-data)
8. [Compare Against WATCHTOWER Behaviour](#phase-8--compare-against-watchtower-behaviour)
9. [Documentation](#phase-9--documentation)

---

## Phase 1 — Locate Every Write

Exhaustive search (`grep -rn "wt_provisioning_edges"` across the full source tree,
excluding test fixtures and `.pyc`) found **exactly one production write location**:

| File:Line | Statement |
|---|---|
| `src/ops/provisioning_edges.py:129` | `INSERT INTO wt_provisioning_edges (...) VALUES (...) ON CONFLICT(edge_type, from_wallet, to_wallet) DO UPDATE SET ...` — inside `_upsert_edge()` |

No other INSERT, UPDATE, ORM call, migration script, or backfill/repair script writes
to this table anywhere in `src/`, `scripts/`, or the top-level codebase. The only other
`INSERT INTO wt_provisioning_edges` statements found anywhere in the repository are
direct SQL fixtures inside test files (`tests/test_x29_7_operational_lineage.py`,
`tests/test_x26_8_...py`, `tests/test_x29_4_wallet_quality.py`,
`tests/test_x26_10_1_...py`) — these construct rows directly for unit-test setup and
are never executed in production. `scripts/x50_cross_launch_infrastructure.py` and
`scripts/x61_canonical_validation_audit.py` only **read** from the table (`SELECT *`).
`src/ops/funding_boundary.py:282` contains only a docstring comment referencing the
same upsert *convention*, not an actual write to this table.

**Conclusion: one write function, `_upsert_edge()`, called only via one public
entry point, `capture_provisioning_relationship()`, in `src/ops/provisioning_edges.py`.**

---

## Phase 2 — Trace the Complete Write Path

Full call graph, traced backward from the single write statement to the original
triggering event:

```
wt_provisioning_edges (INSERT/UPSERT)
    ↑ src/ops/provisioning_edges.py:128  _upsert_edge()
        ↑ src/ops/provisioning_edges.py:184,196  capture_provisioning_relationship()
            ↑ src/core/walkback_worker.py:704-715  _capture_provisioning_facts()
                ↑ src/core/walkback_worker.py:988   _process_row()  [FULL_WALKBACK, hop1 is a known subprov]
                ↑ src/core/walkback_worker.py:1015  _process_row()  [FULL_WALKBACK, hop2 found]
                    ↑ src/core/walkback_worker.py:1234  run_loop()  (calls _process_row per queued row)
                        ↑ src/core/walkback_worker.py:1442  `if __name__ == "__main__": run_loop()`
                            ↑ standalone worker process, polling wt_walkback_queue

wt_walkback_queue (the row _process_row consumes)
    ↑ src/core/walkback_queue.py:307  enqueue_migration()
        ↑ triggering event: a token migration is observed (mint-scoped), which
          enqueues that ONE mint for walkback — confirmed via
          `INSERT OR IGNORE INTO wt_walkback_queue` inside enqueue_migration(),
          keyed on `mint` (line 392)
```

**The originating blockchain observation is a single token's migration event**
(anchored per-mint via `enqueue_migration()`), not a subprov-level observation of any
kind. Every downstream step in this call graph — `_process_row()`,
`_find_with_evidence()`, `capture_provisioning_relationship()` — operates on exactly
one mint at a time, inherited from that one enqueue event.

---

## Phase 3 — Determine Exactly What Creates One Row

Traced directly from `src/core/walkback_worker.py:930-1021` (the `FULL_WALKBACK`
branch of `_process_row()`), the branch responsible for the vast majority of Category-3
launches (per X65.14/X65.15's own finding that walkback resolution, not cascade, is
the dominant evidence source for this population):

| Field | Origin, with citation |
|---|---|
| `to_wallet` (for `SUBPROV_TO_CREATOR` edges) | `creator` — sourced from `wt_walkback_queue.creator` (already populated at enqueue time) OR, if NULL, from `_recover_creator_from_db()` (`walkback_worker.py:750-793`), which reads `wt_watchtower_launches.creator_wallet` → `token_analysis.earliest_tx_creator`/`pf_ws_creator` → `migrated_tokens.creator`, **in that priority order, zero RPC**. In every case, `creator` is a **wallet already known, from a separate table, to be this specific mint's launch creator** — never discovered by this write path itself. |
| `from_wallet` (for `SUBPROV_TO_CREATOR` edges) | `hop1` — the result of `_find_with_evidence(creator, rpc, ops, before_signature=create_sig, source_mint=mint, hop_depth=1)` (line 950-952), which calls `_find_funder_via_rpc(wallet=creator, ...)` (line 340) — this function's own docstring (line 346) states it "collects all valid funders within the bounded tx window" **for the wallet passed in**, i.e. it searches **who funded the creator**, walking backward from a single already-identified creator wallet. It never enumerates the funder's own other outbound transfers. |
| `mint` (`source_mint`) | The mint the enqueued `wt_walkback_queue` row is for — inherited unchanged through the entire call chain from `enqueue_migration()`. |
| `edge_type` | Hardcoded literal (`EDGE_SUBPROV_TO_CREATOR` or `EDGE_TREASURY_TO_SUBPROV`), selected by which call site invokes `_upsert_edge()` — never derived from data. |

**Direct answer to "what blockchain observation causes one row to be written?"**: the
observation is **"wallet X funded already-known-creator Y before Y's CREATE
instruction"** — a single backward-directed funding-transaction lookup anchored on a
specific mint's already-resolved creator, discovered via `_find_funder_via_rpc()`
called with the creator as the search target. It is never "wallet X, a subprov, has
these N outbound transfers" — the write path has no code path that enumerates a
subprov's transfers independent of a specific mint's creator.

---

## Phase 4 — Creator Filtering

Direct, per-question answers, each with source citation:

- **Is `token_analysis` consulted?** Yes — inside `_recover_creator_from_db()`
  (`walkback_worker.py:774-781`), but only as a fallback to *find* the creator's
  identity when `wt_walkback_queue.creator` is NULL. It is never consulted to check
  whether a fan-out recipient is a creator; it supplies the creator identity that the
  entire walk is then anchored on.
- **Is CREATE detection required?** Indirectly, yes — the entire premise of
  `wt_walkback_queue` (populated by `enqueue_migration()`) is a migrated token, which
  by definition already had a CREATE event. The walk never runs for a wallet that
  hasn't already produced a confirmed launch.
- **Is creator identity required?** Yes, absolutely — `_process_row()`'s
  `FULL_WALKBACK` branch (line 932) explicitly checks `if not creator:` and attempts
  recovery; if recovery fails, it calls `_mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND",
  ...)` and returns (line 941) **without ever calling
  `capture_provisioning_relationship()`**. A row is structurally impossible to write
  for this mint without a resolved creator.
- **Is launch reconstruction required?** Yes, in the sense that `mint` must already be
  a real, migrated launch to have been enqueued at all (`enqueue_migration()`).
- **Is walkback required?** Yes — this is the *only* production write path
  (Phase 1). No other module or process writes to this table.

**Can any recipient be written, or only a confirmed creator?** **Only a confirmed
creator.** There is no code path anywhere in `_process_row()`, `_capture_provisioning_facts()`,
or `capture_provisioning_relationship()` that accepts an arbitrary wallet and checks
*whether* it is a creator before deciding to write. The `to_wallet` parameter passed
into every `SUBPROV_TO_CREATOR` call is always literally the variable named `creator`
— a wallet whose creator-status was established **before** this write path ever runs,
by a separate, upstream process (the migration-detection/attribution pipeline that
populates `wt_walkback_queue.creator` / `token_analysis.earliest_tx_creator` in the
first place). This directly and completely confirms X65.16's inference — now proven
from the implementation, not the data.

---

## Phase 5 — Cardinality

**One row is one *distinct wallet-pair relationship*, accumulated across however many
separate launches (mints) independently rediscovered the same funding edge** — not one
row per launch, and not one row per raw transfer.

Proof, from the schema and write logic directly:

- `UNIQUE(edge_type, from_wallet, to_wallet)` (`provisioning_edges.py:62`) is the
  table's only uniqueness constraint — **not** `mint`/`source_mint`. A second launch
  whose walkback resolves the identical (subprov, creator) pair (which cannot actually
  happen for `SUBPROV_TO_CREATOR`, since a creator is single-use by construction
  elsewhere, but *can* happen for `TREASURY_TO_SUBPROV`, where many launches' walks all
  resolve the same treasury→subprov pair) does **not** insert a second row — it fires
  the `ON CONFLICT ... DO UPDATE` branch (`provisioning_edges.py:134-141`), which
  increments `observation_count` and advances `last_observed_by_flex`, overwriting
  `source_mint` with the most recent mint's value (`source_mint = excluded.source_mint`,
  line 141).
- Therefore: **many launches → one accumulated row**, for edges observed across
  multiple mints. **One launch → at most one new row** (or zero, if the edge already
  existed from a prior launch — in which case that launch instead advances the
  existing row's `observation_count`/`last_observed_by_flex`/`source_mint`).
- `source_mint` on any given row reflects only the **most recently processed** launch
  that touched this edge, per the `COALESCE`/`excluded` overwrite pattern — **it is not
  a reliable record of every launch that ever contributed to this row's
  `observation_count`.**

**Answer to "is one row one launch, one creator, one recipient, one transfer, or one
provisioning event?"**: **one row is one *(from_wallet, to_wallet, edge_type)*
relationship**, i.e. structurally closest to "one creator" for `SUBPROV_TO_CREATOR`
rows specifically (since `to_wallet` is always a creator and creators are single-use,
the practical effect is one row per distinct creator ever funded by that subprov) —
but the row itself is **not** scoped to one launch, one transfer, or one provisioning
event; `observation_count` and the overwritten `funding_*` fields represent an
accumulation across however many times this platform's walkback independently
rediscovered the identical wallet pair.

---

## Phase 6 — Test the Fan-Out Interpretation

Direct answer, following from Phase 3–5's proven facts:

**"68 sibling edges" for a given subprov means exactly: 68 distinct wallets that
this subprov's outbound funding transaction was independently traced to, where each
of those 68 wallets was *already independently established, by a separate process, to
be the creator of a real, migrated token*, before the walkback for that specific token
ever ran.**

This is **not** "68 total recipients" (the write path never observes or records a
non-creator recipient at all — Phase 4). It **is not** "68 historical creator
relationships accumulated across many launches" in the sense of double-counting one
launch multiple times — `SUBPROV_TO_CREATOR`'s `UNIQUE(edge_type, from_wallet,
to_wallet)` constraint, combined with each `to_wallet` being a distinct single-use
creator (by the project's own already-established creator-freshness invariant), means
each of the 68 rows corresponds to a genuinely distinct launch. It **is** best
described as **"68 distinct, confirmed creator-launch relationships this subprov has
been independently walked-back to"** — a real, non-inflated count of distinct
creators, but structurally **incapable of ever including a buy-swarm participant**,
because the write path that produces each row only ever runs *after* creator status
is already known, for that one specific wallet, and never scans the subprov's full
transaction history to enumerate anything else.

---

## Phase 7 — Relationship to Raw Blockchain Data

**Does `wt_provisioning_edges` ever observe all recipients?** No — proven directly:
there is no code path in `_process_row()`, `_find_funder_via_rpc()`, or
`capture_provisioning_relationship()` that enumerates a subprov's own outbound
transaction history independent of a specific creator target. The only RPC call that
touches the subprov's activity is `_find_funder_via_rpc(creator, ...)` — a search
**for the creator's funder**, which by construction only ever surfaces the one funding
transaction relevant to that one creator, never the subprov's other transfers.

**Does it observe only creator recipients?** Yes — this is the entire mechanism
(Phase 3/4/6).

**Does it observe only confirmed creators?** Yes, specifically: "confirmed" here means
established via `wt_walkback_queue.creator` or `_recover_creator_from_db()`'s
priority-ordered lookup against `wt_watchtower_launches`/`token_analysis`/
`migrated_tokens` — all zero-RPC, pre-existing records, not a fresh on-chain
CREATE-detection performed by this write path itself.

**Does it observe only analysed launches?** Yes — every row's existence is gated on a
`wt_walkback_queue` row (itself gated on `enqueue_migration()`, i.e. only migrated
tokens are ever enqueued) having successfully reached the `FULL_WALKBACK`/`PARTIAL_SUBPROV`
success branches of `_process_row()`.

**Where does filtering occur, precisely?** At `src/core/walkback_worker.py:932`
(`if not creator:`) — a launch with no resolvable creator identity never reaches
`_capture_provisioning_facts()` at all, and is marked `NO_ATTRIBUTION_FOUND` instead
(line 941). There is no *separate* filtering step that discards non-creator
recipients after observing them — non-creator recipients are simply **never
observed** by this code path in the first place, because the search direction is
always creator→funder (backward), never subprov→all-recipients (forward).

---

## Phase 8 — Compare Against WATCHTOWER Behaviour

The documented WATCHTOWER model:

```
Treasury → SubProvider → fan-out to many accounts → one account becomes launch creator
```

**Is `wt_provisioning_edges` intended to represent the complete SubProvider fan-out, or
only the creator selected from that fan-out?**

**Only the creator selected from that fan-out — proven directly from the
implementation, not inferred.** The write path's own search direction
(`_find_funder_via_rpc(wallet=creator, ...)`, always searching backward from an
already-confirmed creator) makes it structurally impossible for this table to ever
represent "many accounts" — it only ever represents the **one** account, per launch,
that the walkback process already knows became a creator. The module's own docstring
(`provisioning_edges.py:1-18`) is consistent with this: it describes itself as
capturing "observed structural relationships discovered during a successful walkback
hop" — a walkback hop being, by the entire codebase's own naming and design (traced in
Phase 2), a single mint-anchored backward trace, never a forward subprov-fan-out scan.

**What does `wt_provisioning_edges` actually represent, if not fan-out?** It
represents the **accumulated set of distinct funding relationships this platform's
walkback process has successfully resolved**, one edge per distinct wallet pair,
where a `SUBPROV_TO_CREATOR` edge specifically means "this subprov funded this
already-confirmed creator, and we have independently walked back to prove it, at
least once." Its `observation_count`/`last_observed_by_flex` fields describe how many
times walkback re-confirmed the same already-known pair, not how many total accounts
the subprov ever funded.

---

## Phase 9 — Documentation

**`wt_provisioning_edges` stores**: an append-only, deduplicated ledger of
funding-relationship *edges* (`TREASURY_TO_SUBPROV` or `SUBPROV_TO_CREATOR`) that this
platform's walkback pipeline (`src/core/walkback_worker.py`) has independently
confirmed while resolving a specific migrated token's creator-funding lineage,
backward from that token's already-known creator. Each row is uniquely keyed on
`(edge_type, from_wallet, to_wallet)` and accumulates `observation_count` /
`last_observed_by_flex` / the most-recently-seen funding details across every launch
whose walkback rediscovered the same wallet pair.

**What one row means**: "This platform has independently confirmed, via a
backward-directed walkback search anchored on at least one already-known token
creator, that `from_wallet` funded `to_wallet` (as either a treasury→subprov or
subprov→creator relationship), at least once, most recently at
`last_observed_by_flex`."

**What one row does NOT mean**:
- It does **not** mean `from_wallet`'s total outbound fan-out is limited to the rows
  present for it in this table — the table only contains recipients that were
  *already* confirmed creators before the row was ever written; non-creator
  recipients (including any buy-swarm participants) are structurally invisible here.
- It does **not** mean one row corresponds to one specific launch — `source_mint`
  reflects only the most recently processed launch touching this edge, and
  `observation_count` can span many launches for `TREASURY_TO_SUBPROV` edges.
- It does **not** represent a raw, complete, or time-ordered transaction record — it
  is a deduplicated, overwritten-in-place summary of the *latest* known
  characteristics of a recurring relationship.

**Appropriate uses**:
- Counting distinct, confirmed creator-launch relationships a subprov has been walked
  back to (a real, non-inflated *creator* count).
- Establishing whether a treasury→subprov or subprov→creator relationship has ever
  been independently confirmed by walkback, for lineage/graph-reconstruction purposes.
- Reading `funding_mechanism`/`funding_amount_sol`/`funding_tx_signature` as the most
  recently observed characteristics of a known, recurring relationship.

**Inappropriate uses**:
- Treating a subprov's sibling-edge count as its total fan-out, recipient count, or
  "how many accounts did this subprov ever pay" — it measures confirmed-creator
  count only.
- Using this table to distinguish creator-provisioning from buy-swarm activity — it
  cannot, by construction, ever contain a buy-swarm wallet.
- Assuming `source_mint` enumerates every launch that contributed to a row's
  `observation_count` — it reflects only the latest one.

**Common misinterpretation this audit corrects**: X65.14 and X65.15 both used this
table's per-subprov row count ("sibling edges") as a general-purpose "independent
fan-out evidence" signal supporting Campaign-classifier precision conclusions. That use
remains valid for what it actually measured (distinct confirmed-creator count, a real
and meaningful signal of subprov activity level) but was imprecisely labeled as
"fan-out" in those reports. X65.16 already identified this gap empirically, from
observed data; this audit now proves, from the source code itself, exactly why that
gap exists and exactly what the correct label for the metric should be going forward:
**"confirmed distinct creators funded," not "total fan-out" or "total recipients."**

### Deliverables

Complete write-path call graph tracing from the single INSERT statement back to the
originating migration-detection trigger (Phase 1/2); full field-by-field origin proof
for one row, including the exact backward-search mechanism that makes creator-only
writes structurally guaranteed (Phase 3/4); a precise cardinality determination —
one row per distinct wallet-pair, accumulated across launches, not one row per launch
(Phase 5); a corrected, code-proven definition of what a sibling-edge count actually
measures (Phase 6); confirmation that no raw/complete recipient enumeration ever
occurs anywhere in this write path (Phase 7); a direct comparison against the
documented WATCHTOWER fan-out model showing this table represents only the
creator-selected-from-fan-out, never the fan-out itself (Phase 8); and corrected
documentation language for future project use (Phase 9). No code was changed; no
database writes occurred; no UI was modified.
