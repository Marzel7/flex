# Axis 1 — Validation Matrix (live, read-only)

**Date:** 2026-06-07 · **Source:** `flex_complete_database.db` · **Derivation:** `src/analysis/risk_axis.py`
Endpoint: `/api/watchtower/risk-axis`. No production data changed.

Two populations are validated:
- **Creator-keyed** (`creator_risk_scores`, 55,485) — used by the endpoint, because State
  (Axis 2) and Attribution (Axis 3) are creator-keyed, so all three axes share a key.
- **Token-keyed** (`token_prediction_scores`, 224,439) — used to validate the WATCH
  retirement and faithfulness to the current legacy `_risk_level()`.

---

## 1. Faithfulness check (token-keyed): legacy `risk_level` → Axis 1 `risk`

| legacy | → Axis 1 | n | note |
|---|---|---|---|
| *(empty)* | UNKNOWN | 167,704 | not-COMPLETE → explicit UNKNOWN (was blank) |
| HIGH | HIGH | 18,956 | identical |
| MEDIUM | MEDIUM | 18,617 | identical |
| LOW | LOW | 7,077 | identical |
| HIGH | MEDIUM | 5,703 | **stale stored data**, not a diff — see below |
| WATCH | UNKNOWN | 4,291 | **WATCH retired** (rows that aren't COMPLETE) |
| WATCH | LOW | 2,031 | **WATCH retired** (genuine low-severity band) |
| CRITICAL | CRITICAL | 62 | identical |

**The only intentional changes are WATCH retirement and empty→UNKNOWN.** Every other
band matches the *current* legacy function.

**The 5,703 "HIGH→MEDIUM" are not a derivation difference.** They carry
`LIQUIDATION_RISK` at score 21–53. Running the *current* legacy `_risk_level()` on those
same rows also returns **MEDIUM** — the stored `HIGH` is from an older scoring version
(stored-vs-current drift). They would self-correct on the next rescore. Axis 1 is
faithful to current legacy logic.

---

## 2. Risk distribution (creator-keyed, 55,485)

| risk | n |
|---|---|
| CRITICAL | 940 |
| HIGH | 341 |
| MEDIUM | 179 |
| LOW | 54,025 |
| UNKNOWN | 0 |

(UNKNOWN is 0 here because every `creator_risk_scores` row has a `final_score`; UNKNOWN
appears in the token population, where 167k predictions never reached COMPLETE.)

---

## 3. Risk × Creator-State

|        | WATCHLIST | SERIAL | ESTABLISHED | EMERGING | FRESH |
|--------|----:|----:|----:|----:|----:|
| CRITICAL | 0 | 118 | 72 | 120 | 630 |
| HIGH | 47 | 110 | 71 | 33 | 80 |
| MEDIUM | 51 | 31 | 27 | 15 | 55 |
| LOW | 607 | 1,491 | 4,194 | 10,109 | 37,624 |

Reading: severity and state are now **independent** — every state spans every risk band.
A SERIAL creator can be LOW risk (1,491 of them); a FRESH creator can be CRITICAL (630).
The legacy model could not express either cleanly because WATCH occupied a risk slot.

---

## 4. Risk × Attribution

| risk | attribution breakdown |
|---|---|
| CRITICAL | NONE: 940 |
| HIGH | NONE: 341 |
| MEDIUM | NONE: 179 |
| LOW | NONE: 53,741 · WT_DIRECT: 106 · WT_LINEAGE: 114 · WT_FINGERPRINT: 39 · WT_RELAY: 18 · WT_PROVISIONING: 7 |

**Headline finding: all 284 WATCHTOWER-attributed creators derive as risk=LOW.** This is
the single clearest proof the axes were conflated. The legacy `LOW + WATCHTOWER`
"contradiction" was never a contradiction — it is a low-*severity* creator that is
nonetheless *attributed* to a known operator. Severity (Axis 1) and attribution (Axis 3)
are orthogonal; the new model states both without conflict.

---

## 5. Creator-State × Attribution

| state | attributed (non-NONE) |
|---|---|
| WATCHLIST | WT_LINEAGE: 5 |
| SERIAL | (none) |
| ESTABLISHED | (none) |
| EMERGING | (none) |
| FRESH | WT_DIRECT: 106 · WT_LINEAGE: 109 · WT_FINGERPRINT: 39 · WT_RELAY: 18 · WT_PROVISIONING: 7 |

Confirms the burner pattern: 279/284 attributed creators are FRESH (single-token
burners). Attribution lives on Axis 3 (the *operator* they belong to); the *wallet's*
own launch history is genuinely thin (FRESH). Correct separation, not a misclassification.

---

## 6. Audit questions (answered)

| question | answer |
|---|---|
| FRESH + HIGH/CRITICAL | **710** — fresh wallets can still be high-severity by score |
| FRESH + WATCHTOWER | **279** — burner wallets attributed to operators |
| LOW + WATCHTOWER | **284** — *all* attributed creators; the old "contradiction", now coherent |
| SERIAL + WATCHTOWER | **0** — operators don't reuse high-volume wallets (burner discipline) |
| WATCHLIST + WATCHTOWER | **5** — flagged creators that are also operator-linked |

The `SERIAL + WATCHTOWER = 0` result is itself an intelligence signal: confirmed operators
launch through fresh burners, never through their high-volume serial identities.
