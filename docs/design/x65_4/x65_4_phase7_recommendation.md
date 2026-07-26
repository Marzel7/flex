# X65.4 — Phase 7: Recommend Correct Behaviour

No changes were implemented in this task, per the explicit instruction.
This phase states a recommendation only.

## The two candidate models

**A. Direct creator lineage**: Treasury → SubProv → Creator.

**B. Operational funding graph**: Treasury → SubProv → {Wallet, Wallet,
Wallet, ..., Creator}.

## Recommendation: Model B (Operational Funding Graph) better reflects operational reality

### Why

- **Phase 3's direct replay evidence**: 38 of 43 confirmed WATCHTOWER
  launches (88.4%) show genuine multi-recipient fan-out from their
  subprov — ranging from 2 to 481 distinct wrap-close destinations,
  with the confirmed creator being exactly one of them. Model A's
  premise (subprov funds the creator, singularly) is directly
  contradicted by the platform's own already-recorded on-chain
  observations for the overwhelming majority of confirmed launches.
- **The architecture the investigation validated is Model B by
  construction**: the WATCHTOWER provisioning pattern, as directly
  observed (Phase 3), routinely produces dozens to hundreds of wallets
  from a single subprov, of which only one goes on to create a token
  and become "the creator." A topology model that only recognizes
  Model A will systematically describe this operation's most
  characteristic feature — the fan-out itself — as if it never
  happened.
- **The evidence to support Model B is not hypothetical or requiring
  new detection**: `wt_candidate_websocket_watches` already captures
  every wrap-close destination a subprov produces, in real time, with
  no additional RPC or new detection logic required (Phase 1/6). This
  is a data the platform already has and is not using, not a gap that
  requires building new capture infrastructure.
- **Model A is not wrong as a lineage fact — it is incomplete as a
  topology description.** Treasury → SubProv → Creator remains true as
  far as it goes (the creator genuinely was funded by that subprov,
  via that treasury); the problem is that describing this single edge
  as the *entire* topology, when 24-480 sibling wallets from the same
  provisioning cycle exist and are already recorded, misrepresents the
  operation's actual shape to any analyst or downstream classifier
  relying on "topology" to mean "the funding graph."

### Should the classification logic be modified?

**Yes.** Specifically:
- `funding_topology.py`'s Fan-Out/Linear determination should consult
  `wt_candidate_websocket_watches` (subprov's full recorded wrap-close
  destination set) as a primary evidence source, not fall back to
  `wt_provisioning_edges`'s creator-only edge count as if it were a
  complete picture of a subprov's outbound activity.
- The existing `wt_provisioning_edges`/walkback-based signal should be
  retained as a secondary/corroborating source (it captures genuine
  cross-mint subprov reuse, which `wt_candidate_websocket_watches`
  alone does not directly encode) — not replaced outright, since it
  answers a related but distinct question (has this subprov funded
  multiple *different creators* over its lifetime, vs. did this
  subprov's *single provisioning cycle* fan out to multiple wallets).
- This task does not design the specific implementation of that
  change (out of scope per the "do not implement any changes"
  instruction) — only that the evidence source gap identified in
  Phases 1/4/6 is the correct target for a future, separately-scoped
  implementation task.

## What this recommendation does not claim

- It does not claim every `LINEAR` or `UNKNOWN` launch in the current
  4,194-launch population is actually a `FAN_OUT` — Phase 5 found the
  broader population mostly lacks the evidence needed to check this
  directly, and that gap itself (most Discovery launches never
  populate `wt_candidate_websocket_watches` because they're
  walkback-resolved, not live-cascade-detected) is a separate, real
  limitation that a topology-logic fix alone would not close for that
  subset.
- It does not recommend abandoning creator-ancestry evidence — both
  models' evidence remain true and useful; the recommendation is to
  stop treating Model A's evidence as if it were a complete substitute
  for Model B's, when the platform already has the data to distinguish
  them.
