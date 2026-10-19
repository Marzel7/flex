"""
Knowledge Layer — rule registry.

Each rule is an independent object that:
  - declares what it detects (rule_id, category, description)
  - reports whether it applies to a given entity's evidence (applies)
  - derives a KnowledgeItem when it does (derive)

To add a new rule: subclass KnowledgeRule, implement applies() and derive(),
then call REGISTRY.register(MyRule()). No if/elif chains anywhere.

Evidence dict keys used by rules (assembled by engine.py):
  launch_count          int   — launches in wt_farm_launches
  funding_mode          str   — "WRAP_CLOSE" | "PLAIN_TRANSFER" | "UNKNOWN"
  known_address_entry   AddressEntry | None  — from loader.lookup_address
"""

from __future__ import annotations

import abc
from typing import Any

from src.knowledge.models import (
    CATEGORY_BEHAVIOUR,
    CATEGORY_EXECUTION,
    CATEGORY_FUNDER,
    CATEGORY_TOOLING,
    KnowledgeItem,
)

Evidence = dict[str, Any]

PERSISTENT_LAUNCHER_THRESHOLD = 3   # launches >= this → PERSISTENT_LAUNCHER


# ── Base class ─────────────────────────────────────────────────────────────────

class KnowledgeRule(abc.ABC):
    rule_id:     str
    category:    str
    description: str

    @abc.abstractmethod
    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        """Return True if this rule can derive a fact from the given evidence."""

    @abc.abstractmethod
    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        """Return a KnowledgeItem. Only called when applies() returns True."""


# ── Registry ───────────────────────────────────────────────────────────────────

class _RuleRegistry:
    def __init__(self) -> None:
        self._rules: list[KnowledgeRule] = []

    def register(self, rule: KnowledgeRule) -> None:
        self._rules.append(rule)

    def apply_all(self, entity_id: str, evidence: Evidence) -> list[KnowledgeItem]:
        results: list[KnowledgeItem] = []
        for rule in self._rules:
            try:
                if rule.applies(entity_id, evidence):
                    results.append(rule.derive(entity_id, evidence))
            except Exception as exc:
                print(f"[KNOWLEDGE] rule {rule.rule_id} failed for {entity_id}: {exc}")
        return results

    @property
    def rules(self) -> list[KnowledgeRule]:
        return list(self._rules)


REGISTRY = _RuleRegistry()


# ── Execution rules ────────────────────────────────────────────────────────────

class _WrapCloseRule(KnowledgeRule):
    rule_id     = "EXECUTION:WRAP_CLOSE"
    category    = CATEGORY_EXECUTION
    description = "Funder used the WSOL wrap-close pattern to seed creator wallets"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        return evidence.get("funding_mode") == "WRAP_CLOSE"

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_EXECUTION,
            type="WRAP_CLOSE",
            value="WSOL wrap-close creator funding",
            confidence="HIGH",
            source="PATTERN_MATCH",
            provenance="rule:EXECUTION:WRAP_CLOSE",
            supporting_evidence=(
                f"launch_count={evidence.get('launch_count', '?')}",
                "funding_mode=WRAP_CLOSE",
            ),
        )


class _PlainTransferRule(KnowledgeRule):
    rule_id     = "EXECUTION:PLAIN_TRANSFER"
    category    = CATEGORY_EXECUTION
    description = "Funder used plain SOL transfers to seed creator wallets (not wrap-close)"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        return evidence.get("funding_mode") == "PLAIN_TRANSFER"

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_EXECUTION,
            type="PLAIN_TRANSFER",
            value="Plain SOL transfer creator funding",
            confidence="HIGH",
            source="PATTERN_MATCH",
            provenance="rule:EXECUTION:PLAIN_TRANSFER",
            supporting_evidence=(
                f"launch_count={evidence.get('launch_count', '?')}",
                "funding_mode=PLAIN_TRANSFER",
            ),
        )


# ── Behaviour rules ────────────────────────────────────────────────────────────

class _PersistentLauncherRule(KnowledgeRule):
    rule_id     = "BEHAVIOUR:PERSISTENT_LAUNCHER"
    category    = CATEGORY_BEHAVIOUR
    description = (
        f"Funder has launched ≥{PERSISTENT_LAUNCHER_THRESHOLD} tokens — "
        "consistent repeat-launch behaviour"
    )

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        return (evidence.get("launch_count") or 0) >= PERSISTENT_LAUNCHER_THRESHOLD

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        n = evidence.get("launch_count", 0)
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_BEHAVIOUR,
            type="PERSISTENT_LAUNCHER",
            value=f"Persistent launcher ({n} launches)",
            confidence="HIGH",
            source="PATTERN_MATCH",
            provenance="rule:BEHAVIOUR:PERSISTENT_LAUNCHER",
            supporting_evidence=(f"launch_count={n}",),
        )


class _SingleUseRule(KnowledgeRule):
    rule_id     = "BEHAVIOUR:SINGLE_USE"
    category    = CATEGORY_BEHAVIOUR
    description = "Funder has only one recorded launch — may be single-use wallet"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        return (evidence.get("launch_count") or 0) == 1

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_BEHAVIOUR,
            type="SINGLE_USE",
            value="Single-use launcher (1 launch)",
            confidence="MEDIUM",
            source="PATTERN_MATCH",
            provenance="rule:BEHAVIOUR:SINGLE_USE",
            supporting_evidence=("launch_count=1",),
        )


# ── Funder-type rules (address-table driven) ───────────────────────────────────

class _KnownCexRule(KnowledgeRule):
    rule_id     = "FUNDER_TYPE:KNOWN_CEX"
    category    = CATEGORY_FUNDER
    description = "Funder address is a known centralised-exchange hot wallet"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        entry = evidence.get("known_address_entry")
        return entry is not None and entry.family == "cex"

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        entry = evidence["known_address_entry"]
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_FUNDER,
            type="KNOWN_CEX",
            value=entry.label or "Known CEX hot wallet",
            confidence=entry.confidence,
            source="ADDRESS_TABLE",
            provenance=f"knowledge/addresses/cex.yaml:{entry.label}",
            supporting_evidence=(
                f"label={entry.label}",
                f"source={entry.source}",
            ),
        )


class _KnownPlatformRule(KnowledgeRule):
    rule_id     = "FUNDER_TYPE:KNOWN_PLATFORM"
    category    = CATEGORY_FUNDER
    description = "Funder address is a known launch-platform (relay/launchpad) wallet"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        entry = evidence.get("known_address_entry")
        return entry is not None and entry.family == "relay"

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        entry = evidence["known_address_entry"]
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_FUNDER,
            type="KNOWN_PLATFORM",
            value=entry.label or "Known launch platform",
            confidence=entry.confidence,
            source="ADDRESS_TABLE",
            provenance=f"knowledge/addresses/relay.yaml:{entry.label}",
            supporting_evidence=(
                f"label={entry.label}",
                f"source={entry.source}",
            ),
        )


# ── Tooling rules (address-table driven) ──────────────────────────────────────

class _JitoRule(KnowledgeRule):
    rule_id     = "TOOLING:JITO"
    category    = CATEGORY_TOOLING
    description = "Entity address is a Jito infrastructure address (tip account)"

    def applies(self, entity_id: str, evidence: Evidence) -> bool:
        entry = evidence.get("known_address_entry")
        return entry is not None and entry.family == "jito"

    def derive(self, entity_id: str, evidence: Evidence) -> KnowledgeItem:
        entry = evidence["known_address_entry"]
        return KnowledgeItem(
            entity_id=entity_id,
            category=CATEGORY_TOOLING,
            type="JITO",
            value=entry.label or "Jito infrastructure",
            confidence=entry.confidence,
            source="ADDRESS_TABLE",
            provenance=f"knowledge/addresses/jito.yaml:{entry.label}",
            supporting_evidence=(
                f"label={entry.label}",
                f"source={entry.source}",
            ),
        )


# ── Register all rules (order = display order in responses) ───────────────────

REGISTRY.register(_WrapCloseRule())
REGISTRY.register(_PlainTransferRule())
REGISTRY.register(_PersistentLauncherRule())
REGISTRY.register(_SingleUseRule())
REGISTRY.register(_KnownCexRule())
REGISTRY.register(_KnownPlatformRule())
REGISTRY.register(_JitoRule())
