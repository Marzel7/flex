# X29.5 — Operational Topology Semantics Audit

Investigation only, per the brief's "do not propose code." This is a semantic review of `src/ops/funding_topology.py` and how its output is presented in `templates/discovery.html`, grounded in the confirmed WATCHTOWER lineage:

```
ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq   (confirmed subprovider)
        │  funds N provisioning wallets (fan-out behaviour)
        ▼
HZB2FdTaY9dojktV4mwr6rznhR95GmftaK8UPJWCMd4u   (one of the N)
        │  funds the creator
        ▼
HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt   (creator)
```

## What the code actually does today (verified, not assumed)

`funding_topology.py`'s own docstring states its job plainly: *"Answers exactly one question per launch: what does the funding graph look like?"* It assigns exactly one of `FAN_OUT / LINEAR / MULTI_LEVEL_FAN_OUT / MESH / UNKNOWN` **per launch** (i.e., per creator/mint), not per wallet. The underlying data it reads — `wt_provisioning_edges` — is genuinely relational: every row is `edge_type ∈ {TREASURY_TO_SUBPROV, SUBPROV_TO_CREATOR}` with a `from_wallet`/`to_wallet` pair. That table already models the lineage as a graph of *roles connected by edges*. `funding_topology.py`'s classifier then collapses that graph into a single scalar label for the terminal creator, and `operational_intelligence.py`/`discovery.html` present that scalar as the **primary, top-level classification** — "Funding Topology → Behaviour → Mechanism," in that visual order, with FAN_OUT/LINEAR appearing as if they were categories a launch *belongs to*.

Applying this to the ANen/HZB2/HTR9 example: the launch would be classified `FAN_OUT` because `ANen`'s sibling count (`_subprov_sibling_counts`) is >1. But "the launch is Fan-Out" is a strange sentence — the *launch* (a token, a creator) didn't fan out. **`ANen` fanned out.** `HZB2` and `HTR9` did nothing of the sort; `HZB2` forwarded once, `HTR9` received once. The label is real evidence, but it's been attached to the wrong node.

## Answering the four questions

**1. Is "Fan-Out" a wallet type, or a behaviour performed by another operational role?**

It is a behaviour, and specifically a behaviour of exactly one operational role: the subprovider (or, in the Mesh case, a treasury acting as a subprov elsewhere). Fan-out is not a static property a wallet *is* — it's an *action count* a wallet performs (`COUNT(DISTINCT to_wallet)` in `_subprov_sibling_counts`). The current code already computes it that way (as a per-`from_wallet` count); it just then relabels the *launch* with the subprovider's behaviour, rather than keeping the behaviour attached to the subprovider.

**2. Should a provisioning wallet ever itself be classified as Fan-Out?**

No, categorically. A provisioning wallet (HZB2 in the example) sits between the subprovider and the creator; by construction of the walk it has out-degree 1 to the creator it funded (that's what makes it "the provisioning wallet for this launch" rather than a subprovider in its own right). If a wallet in that position *did* show out-degree >1, that would mean it's not a plain forwarding hop at all — it would itself be acting as a subprovider, and the model should re-classify its role, not tag it with a topology label while leaving its role unchanged.

**3. Should Discovery display operational role primarily, with topology as supporting metadata?**

Yes. The current landing view inverts this: `discovery.html`'s top-level drill-down is literally titled "Funding Topology → Behaviour → Mechanism," and a launch's role-bearing identity (`operation_identity`, `canonical_identity`) is rendered further down the page, after the topology/behaviour/mechanism selection UI. An analyst opening a launch sees "Fan-Out" before they see "this creator was funded by a provisioning wallet, itself funded by a confirmed subprovider." The role *is* the finding; the topology is one piece of evidence supporting it. Today's ordering asks the analyst to mentally invert the presentation to get back to the operational story.

**4. Is the current topology vocabulary sufficient to discover unknown operations, or should topology describe relationships while separate roles describe entities?**

The vocabulary itself (Linear / Fan-Out / Multi-Level Fan-Out / Mesh) is fine as a *relationship* vocabulary — it correctly distinguishes "one funder, one recipient" from "one funder, many recipients" from "funder-of-funders" from "treasury acting as a peer subprov." The problem is exclusively that it's applied to the wrong unit. As a discovery tool for *unknown* operations, a per-launch scalar is actively worse than a per-edge/per-wallet relationship record: two structurally identical subprovider wallets funding different creators currently produce two independent per-launch FAN_OUT rows with no queryable link between them ("this is the *same* subprovider fanning out again") unless you separately go read `wt_provisioning_edges`. If topology were instead a property of the *edge* (or of the wallet's aggregate edge set), a new, previously-unseen subprovider would surface the moment its sibling count crossed 1 — visible as "wallet X is now fanning out to 2 recipients," a discovery signal — rather than only becoming visible retroactively once enough individual launches have each independently been labeled FAN_OUT.

## Conflation diagnosis

The current model conflates three genuinely independent things into one field:

- **Operational role** — what kind of participant is this wallet in the lineage (Creator / Provisioning Wallet / Subprovider / Treasury)? This is a property of a *node*.
- **Topology / relationship shape** — how many downstream recipients does a given funder have, and at what depth (Linear vs Fan-Out vs Multi-Level vs Mesh)? This is a property of a *node's edge set* (specifically: the subprovider's or treasury's out-degree and depth), not of the launch or the creator.
- **Behaviour** — timing/pattern signals like Rapid Birth or Burst Launch (already correctly modeled as additive tags in `operational_behaviour_tags.py`, independent of topology). This part of the existing three-axis model is *not* the problem; it's a good template for how role and topology should also work.

X29.1's own three-axis design (Funding Topology / Operational Behaviour / Funding Mechanism) already got the *additive-tags-vs-exclusive-classification* distinction right for Behaviour and Mechanism. The audit's finding is that Topology was built as the third *exclusive* axis when it should not be exclusive at the launch level at all — it's evidence that belongs on the subprovider/treasury node, expressed as a per-node fact ("this subprovider has fanned out to N wallets"), and role (Creator/Provisioning Wallet/Subprovider/Treasury) is the missing fourth axis that should have been primary all along.

## Recommended conceptual model (no code — description only)

Replace the single "Topology" label on a launch with an explicit **role-annotated lineage chain**, where each node carries its role plus topology/behaviour as node-level metadata rather than one topology value describing the whole launch:

```
Creator                          HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt
  ↑ funded by
Provisioning Wallet               HZB2FdTaY9dojktV4mwr6rznhR95GmftaK8UPJWCMd4u
  ↑ funded by
Subprovider (fanned out to N)     ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq
  ↑ funded by
Treasury                          <root treasury, if known>
```

Each node's card carries only the metadata that is actually true of *that* node:

- **Creator**: historical launch count for this address (fresh vs. repeat creator), the single funding edge it received.
- **Provisioning Wallet**: forwarding count (normally 1 — the presence of >1 is itself a signal the role assignment needs revisiting, per Q2 above), time-to-forward.
- **Subprovider**: fan-out count (N distinct provisioning wallets funded), creator count reached transitively, whether it is itself a child of another subprovider (Multi-Level), historical launches attributable through it, observed funding mechanism(s) (wrap-close vs plain transfer, from the existing X29's Funding Mechanism axis).
- **Treasury**: subprovider count funded, whether it also appears as a subprov elsewhere (Mesh signal), aggregate launches under this root.

Topology terms (Fan-Out, Multi-Level Fan-Out, Mesh, Linear) become **descriptions attached to the Subprovider/Treasury node's edge set**, not a classification of the launch or of the Creator/Provisioning Wallet nodes. "Fan-Out" stops being an answer to "what is this launch?" and becomes an answer to "how does this subprovider behave?" — which is both more accurate and more useful for spotting new operations, since it's now a property that accrues and updates as more launches are observed through the same subprovider, rather than a label re-derived independently and identically for every one of that subprovider's launches.

This generalizes beyond WATCHTOWER cleanly: any future operation with a different chain depth or shape (a treasury funding a creator directly with no subprov, a subprov that is itself two hops deep) is just a shorter or longer role chain with the same four building blocks (Creator / Provisioning Wallet / Subprovider / Treasury), rather than requiring a new topology enum value each time a new shape is observed.
