# Strategy PnL Reference

## Overview

Every token entry creates a `trade_simulations` record. As price moves, `process_cascade_sells()` evaluates all 10 strategies independently and writes exit records to `trade_simulation_sells`. The UI (`_apply_strategy_view`) reads these records as source of truth — it does not recompute from ratios.

**`trade_simulations`** — position level. `status = OPEN | CLOSED` is driven by the cascade strategy only.

**`trade_simulation_sells`** — strategy-level exits. UNIQUE on `(simulation_id, strategy, sell_type, target)`.

---

## Strategies

| Strategy | Description | Applies To |
|---|---|---|
| `cascade` | Sells 1/6 at each of 6 targets: 1.5x, 2.5x, 3.5x, 5x, 7x, 10x. Remaining sold at 50% stop. | All risk levels |
| `target_1_5` | All-in sell at 1.5x | All risk levels |
| `target_2_5` | All-in sell at 2.5x | All risk levels |
| `target_3` | All-in sell at 3x | All risk levels |
| `target_3_5` | All-in sell at 3.5x | All risk levels |
| `target_5` | All-in sell at 5x | All risk levels |
| `target_7` | All-in sell at 7x | All risk levels |
| `target_10` | All-in sell at 10x | All risk levels |
| `peak` | Records every new peak; shows best-ever price history | All risk levels |
| `watch_trailing` | Trailing stop: if peak ≥ 3x, exit when price drops to 50% of peak. If peak < 3x, exit at 50% fixed stop. | WATCH only |

---

## PnL Formula

```
realised_usd = entry_usd × fraction × (mc_at_hit / entry_mc)
pnl_usd      = realised_usd − (entry_usd × fraction)
```

`fraction = 1.0` for all-in strategies and stop sells.  
`fraction = 1/6` (exact) for each cascade tranche.

For cascade, total PnL = sum of all tranche records (targets + any stop record).

---

## RISK Levels

RISK determines which tokens appear in a given strategy view, and also whether `watch_trailing` is evaluated.

| Risk | Watch Trailing | Description |
|---|---|---|
| WATCH | Yes | Highest-conviction tokens |
| LOW | No | Standard filters |
| MEDIUM | No | Standard filters |
| HIGH | No | Aggressive filters |

When a RISK filter is selected in the UI, only tokens with `token_prediction_scores.risk_level = <selected>` appear.

---

## Worked Examples

All four examples below use WATCH-risk tokens.

---

### Sim 255 — Big winner (13.88x peak, 4.92x exit)

**Position:** entry $9.47, entry MC $84,029, peak MC $1,166,525, exit_sol 0.4917 (4.92x)

| Strategy | Outcome | mc_at_hit | realised | PnL |
|---|---|---|---|---|
| `cascade` 1.5x | Target hit (1/6) | 1.50x | $2.37 | +$0.79 |
| `cascade` 2.5x | Target hit (1/6) | 2.50x | $3.94 | +$2.37 |
| `cascade` 3.5x | Target hit (1/6) | 3.50x | $5.52 | +$3.94 |
| `cascade` 5x | Target hit (1/6) | 5.00x | $7.89 | +$6.31 |
| `cascade` 7x | Target hit (1/6) | 7.00x | $11.04 | +$9.47 |
| `cascade` 10x | Target hit (1/6) | 10.00x | $15.78 | +$14.20 |
| **cascade total** | | | **$46.54** | **+$37.08** |
| `target_1_5` | Hit 1.5x → exit all | 1.50x | $14.20 | +$4.73 |
| `target_2_5` | Hit 2.5x → exit all | 2.50x | $23.67 | +$14.20 |
| `target_3` | Hit 3x → exit all | 3.00x | $28.40 | +$18.93 |
| `target_3_5` | Hit 3.5x → exit all | 3.50x | $33.13 | +$23.67 |
| `target_5` | Hit 5x → exit all | 5.00x | $47.33 | +$37.87 |
| `target_7` | Hit 7x → exit all | 7.00x | $66.27 | +$56.80 |
| `target_10` | Hit 10x → exit all | 10.00x | $94.67 | +$85.20 |
| `peak` | Best peak at 13.88x | 13.88x | $131.42 | +$121.95 |
| `watch_trailing` | No sell record → still open / did not fire | — | — | unrealised |

**Cascade calculation detail:**
- Each tranche: `entry_usd × (1/6) × ratio`
- At 1.5x: `$9.47 × (1/6) × 1.5 = $2.37` → PnL = `$2.37 − ($9.47 × 1/6) = +$0.79`
- No stop needed: all 6 tranches were sold

**target_10 calculation:**
- `$9.47 × 1.0 × 10.0 = $94.67` → PnL = `$94.67 − $9.47 = +$85.20`

---

### Sim 449 — Monster winner (15.68x peak, 4.92x exit)

**Position:** entry $9.12, entry MC $42,320, peak MC $663,767, exit_sol 0.4917 (4.92x)

| Strategy | Outcome | mc_at_hit | realised | PnL |
|---|---|---|---|---|
| `cascade` | All 6 targets hit | 1.5x–10x | $44.84 total | +$35.72 |
| `target_1_5` | Hit 1.5x | 1.50x | $13.68 | +$4.56 |
| `target_2_5` | Hit 2.5x | 2.50x | $22.80 | +$13.68 |
| `target_3` | Hit 3x | 3.00x | $27.36 | +$18.24 |
| `target_3_5` | Hit 3.5x | 3.50x | $31.92 | +$22.80 |
| `target_5` | Hit 5x | 5.00x | $45.60 | +$36.48 |
| `target_7` | Hit 7x | 7.00x | $63.85 | +$54.73 |
| `target_10` | Hit 10x | 10.00x | $91.21 | +$82.09 |
| `peak` | Best at 15.68x | 15.68x | $143.06 | +$133.94 |
| `watch_trailing` | Peak ≥ 3x → trailing stop at 50% of peak = 7.84x. Fired at 6.63x | 6.63x | $60.47 | **+$51.35** |

**watch_trailing calculation:**
- Peak was 15.68x → trailing floor = `15.68 × 0.5 = 7.84x`
- Price dropped through 7.84x floor, stop fired at 6.63x
- `$9.12 × 1.0 × 6.63 = $60.47` → PnL = `$60.47 − $9.12 = +$51.35`

---

### Sim 370 — Partial winner (5.33x peak, 2.63x exit)

**Position:** entry $9.13, entry MC $52,863, peak MC $281,559, exit_sol 0.2627 (2.63x)

| Strategy | Outcome | mc_at_hit | realised | PnL |
|---|---|---|---|---|
| `cascade` 1.5x | Target hit | 1.50x | $2.28 | +$0.76 |
| `cascade` 2.5x | Target hit | 2.50x | $3.80 | +$2.28 |
| `cascade` 3.5x | Target hit | 3.50x | $5.33 | +$3.80 |
| `cascade` 5x | Target hit | 5.00x | $7.61 | +$6.09 |
| `cascade` stop | 2/6 unsold at position close (2.63x) | 2.63x | $8.00 | +$5.47 |
| **cascade total** | 4 targets + stop | | **$26.01** | **+$18.40** |
| `target_1_5` | Hit 1.5x | 1.50x | $13.70 | +$4.57 |
| `target_2_5` | Hit 2.5x | 2.50x | $22.83 | +$13.70 |
| `target_3` | Hit 3x | 3.00x | $27.39 | +$18.26 |
| `target_3_5` | Hit 3.5x | 3.50x | $31.96 | +$22.83 |
| `target_5` | Hit 5x | 5.00x | $45.65 | +$36.52 |
| `target_7` | Not reached — backfilled at exit 2.63x | 2.63x | $24.01 | +$14.88 |
| `target_10` | Not reached — backfilled at exit 2.63x | 2.63x | $24.01 | +$14.88 |
| `peak` | Latest peak at 5.33x | 5.33x | $48.63 | +$39.50 |
| `watch_trailing` | Peak ≥ 3x → floor = `5.33 × 0.5 = 2.67x`. Dropped through at 2.63x | 2.63x | $23.99 | **+$14.85** |

**Cascade stop detail:**
- 4 targets hit (1.5x, 2.5x, 3.5x, 5x) → 4/6 sold, 2/6 remain
- Stop on remaining: `$9.13 × (2/6) × 2.63 = $8.00` → PnL = `$8.00 − ($9.13 × 2/6) = +$5.47`

**watch_trailing calculation:**
- Peak 5.33x → trailing floor = `5.33 × 0.5 = 2.67x`
- Price reached 2.63x ≤ 2.67x → stop fired
- `$9.13 × 1.0 × 2.63 = $23.99` → PnL = `$23.99 − $9.13 = +$14.85`

---

### Sim 438 — Moderate winner (4.03x peak, 1.91x exit)

**Position:** entry $9.16, entry MC $45,085, peak MC $181,785, exit_sol 0.1912 (1.91x)

| Strategy | Outcome | mc_at_hit | realised | PnL |
|---|---|---|---|---|
| `cascade` 1.5x | Target hit (1/6) | 1.50x | $2.29 | +$0.76 |
| `cascade` 2.5x | Target hit (1/6) | 2.50x | $3.82 | +$2.29 |
| `cascade` 3.5x | Target hit (1/6) | 3.50x | $5.34 | +$3.82 |
| `cascade` stop | 3/6 unsold at position close (1.91x) | 1.91x | $8.76 | +$4.18 |
| **cascade total** | | | **$20.21** | **+$11.05** |
| `target_1_5` | Hit 1.5x | 1.50x | $13.74 | +$4.58 |
| `target_2_5` | Hit 2.5x | 2.50x | $22.91 | +$13.74 |
| `target_3` | Hit 3x | 3.00x | $27.49 | +$18.33 |
| `target_3_5` | Hit 3.5x | 3.50x | $32.07 | +$22.91 |
| `target_5` | Not reached — closed at 1.91x | 1.91x | $17.52 | +$8.36 |
| `target_7` | Not reached — closed at 1.91x | 1.91x | $17.52 | +$8.36 |
| `target_10` | Not reached — closed at 1.91x | 1.91x | $17.52 | +$8.36 |
| `peak` | Latest peak at 4.03x | 4.03x | $36.95 | +$27.78 |
| `watch_trailing` | Peak ≥ 3x → trailing floor = 4.03 × 0.5 = 2.02x. Fired at 1.91x | 1.91x | $17.52 | **+$8.36** |

**watch_trailing trailing stop:**
- Peak was 4.03x ≥ 3x → trailing floor = `4.03 × 0.5 = 2.02x`
- Price dropped to 1.91x ≤ 2.02x → stop fired
- `$9.16 × 1.0 × 1.91 = $17.52` → PnL = `$17.52 − $9.16 = +$8.36`

---

### Sim 424 — Loser (1.53x peak, 0.37x exit)

**Position:** entry $9.22, entry MC $33,111, peak MC $50,663, exit_sol 0.0374 (0.37x)

| Strategy | Outcome | mc_at_hit | realised | PnL |
|---|---|---|---|---|
| `cascade` 1.5x | Target hit (1/6) | 1.50x | $2.31 | +$0.77 |
| `cascade` stop | 5/6 unsold at position close (0.37x) | 0.37x | $2.84 | −$4.84 |
| **cascade total** | | | **$5.15** | **−$4.07** |
| `target_1_5` | Hit 1.5x → exit all | 1.50x | $13.84 | +$4.61 |
| `target_2_5` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `target_3` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `target_3_5` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `target_5` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `target_7` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `target_10` | Not reached — closed at 0.37x | 0.37x | $3.41 | −$5.81 |
| `peak` | Best at 1.53x | 1.53x | $14.11 | +$4.89 |
| `watch_trailing` | Peak < 3x → fixed 50% stop. Fired at 0.37x | 0.37x | $3.45 | **−$5.78** |

**Cascade stop detail:**
- 1 target hit (1.5x) → 1/6 sold, 5/6 remain
- Stop on remaining: `$9.22 × (5/6) × 0.37 = $2.84` → PnL = `$2.84 − ($9.22 × 5/6) = −$4.84`
- Total cascade: realised `$2.31 + $2.84 = $5.15`, PnL `+$0.77 − $4.84 = −$4.07`

**watch_trailing for sub-3x peak:**
- Peak was 1.53x < 3x → fixed stop at 0.50x
- Price dropped through 0.50x to 0.37x → stop fired
- `$9.22 × 1.0 × 0.37 = $3.45` → PnL = `$3.45 − $9.22 = −$5.78`

**Why target_1_5 profits but watch_trailing loses:**
- `target_1_5` exited at exactly 1.5x before the price crashed
- `watch_trailing` held until the 50% fixed stop at 0.37x — it was still long during the crash

---

## Closed Position Backfill

When a position closes (cascade stops out), any strategy that hasn't fired yet gets an exit record written using the actual `exit_sol / entry_sol` ratio as the close price. This ensures every strategy has a complete PnL record for every position.

```python
close_ratio = exit_sol / entry_sol
realised    = entry_usd × fraction × close_ratio
pnl         = realised − (entry_usd × fraction)
```

**Exception:** `watch_trailing` is NOT backfilled from exit_sol, because it's a live trailing stop that may fire at a different price than the cascade exit. It is only written when the trailing condition actually fires while the position is OPEN.

---

## UI Behaviour

- **Strategy dropdown** changes which sell records are shown. Each row reflects that strategy's actual recorded outcome.
- **RISK filter** shows only tokens where `token_prediction_scores.risk_level` matches.
- **Watch Trailing** only appears in data when WATCH tokens are selected (it's only evaluated for WATCH).
- **OPEN positions** show unrealised PnL computed from current live price for strategies that haven't fired.
- **CLOSED positions** show the recorded sell record (realised PnL), never recomputed.
