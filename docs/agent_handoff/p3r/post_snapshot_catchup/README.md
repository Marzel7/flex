# Post-snapshot operation catch-up rule

Whenever an operation detector is activated from a frozen qualification snapshot, run a bounded reconciliation from the qualification high-water to the detector activation boundary with the exact production detector before considering activation complete. Preserve provisional semantics. Do not infer membership from candidate-family membership. WATCHTOWER is excluded from the retroactive reconciliation rule used in this run.
