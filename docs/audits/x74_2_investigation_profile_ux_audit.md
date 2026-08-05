# X74.2 Investigation Profile UX Audit

## Scope

Presentation-only audit of the existing Investigation Profile payload. No resolver, reconciliation, attribution, evidence, API, or persistence behavior was changed.

## Before

The Evidence surface rendered one row per evidence observation when opened. On the 3hJX control this meant 12 supporting rows, including 10 repeated Creator Reuse observations. Infrastructure detail combined treasury, client, relay, and mechanism values into one undifferentiated address surface. Missing evidence exposed internal `Unavailable` labels and full descriptive text. History and profile timelines trusted source order.

## After

Evidence is grouped by evidence type. The 3hJX control presents three supporting evidence summaries instead of 12 observation rows, a 75% reduction; the original observations remain under collapsed Advanced Evidence. Full wallet values appear only inside collapsed Wallet Membership or Infrastructure Wallets disclosures. The default infrastructure summary exposes one abbreviated treasury plus counts, reducing full wallet visibility from 65 values to zero. Missing evidence is rendered as a short label with a consistent Missing state. Launches, evidence observations, identity history, timeline events, audit events, linked Operator Identity governance reviews, and canonical promotion history are sorted newest-first at presentation time.

Across the six named controls, 360 supporting observation rows compress to 18 evidence-type summaries, a 95% reduction. Full addresses visible by default fall to zero; the treasury identity is abbreviated and all complete address sets require expansion.

## Default Summary

The first screen contains only Population Identity, State, Reason, Promotion, and Next Evidence. It contains no evidence rows, wallet lists, transaction identifiers, governance history, or infrastructure dumps.

## Named Controls

- WATCHTOWER — Confirmed Operator summary retained.
- 3SW2 — Confirmed Operator summary retained.
- B48k / Dv34 — Shared Infrastructure identity retained; details compressed.
- 3hJX — Operational Treasury identity retained; 64 launches, 62 creators, 64 provisioning clients, and one treasury remain visible as counts.
- C7Ha — Provisioning Controller identity retained; Review remains the state.
- Infrastructure — Infrastructure identity retained; wallets hidden by default.

## Visual Continuity

The Registry colour palette, compact badges, bordered rows, typography, spacing, and section rhythm remain unchanged. Colour continues to identify disposition and identity, while evidence uses counts and addresses remain behind disclosure.
