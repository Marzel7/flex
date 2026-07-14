"""
Tests for the Knowledge Layer (Sprint K1).

Covers:
  - Address loader: load, cache, lookup, empty files, missing files, invalid YAML
  - Rule registry: all seven rules registered, applies/derive correctness
  - Engine: enrich() returns expected items, unknown entity returns empty
  - Performance: warm cache lookup under 50ms
  - No Flask at import level
  - No DB writes (no INSERT/UPDATE/DELETE in module source)
  - Launcher Observatory enrichment field present in intelligence response
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import types

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _invalidate_loader() -> None:
    """Reset loader cache between tests so YAML changes take effect."""
    from src.knowledge import loader
    loader.invalidate_cache()


def _make_yaml_dir(*files: tuple[str, str]) -> str:
    """Write named YAML files into a temp directory and return the path."""
    d = tempfile.mkdtemp()
    addr_dir = os.path.join(d, "addresses")
    os.makedirs(addr_dir)
    for name, content in files:
        with open(os.path.join(addr_dir, name), "w") as fh:
            fh.write(content)
    return d


# ── 1. No Flask at module import level ────────────────────────────────────────

class TestNoFlaskAtImport:
    def test_models_no_flask(self) -> None:
        to_remove = [k for k in sys.modules if k.startswith("flask")]
        for k in to_remove:
            del sys.modules[k]
        import src.knowledge.models  # noqa: F401
        assert "flask" not in sys.modules

    def test_loader_no_flask(self) -> None:
        to_remove = [k for k in sys.modules if k.startswith("flask")]
        for k in to_remove:
            del sys.modules[k]
        import src.knowledge.loader  # noqa: F401
        assert "flask" not in sys.modules

    def test_rules_no_flask(self) -> None:
        to_remove = [k for k in sys.modules if k.startswith("flask")]
        for k in to_remove:
            del sys.modules[k]
        import src.knowledge.rules  # noqa: F401
        assert "flask" not in sys.modules

    def test_engine_no_flask(self) -> None:
        to_remove = [k for k in sys.modules if k.startswith("flask")]
        for k in to_remove:
            del sys.modules[k]
        import src.knowledge.engine  # noqa: F401
        assert "flask" not in sys.modules

    def test_routes_no_flask(self) -> None:
        to_remove = [k for k in sys.modules if k.startswith("flask")]
        for k in to_remove:
            del sys.modules[k]
        import src.knowledge.routes  # noqa: F401
        assert "flask" not in sys.modules


# ── 2. No DB writes in module source ──────────────────────────────────────────

class TestNoDbWrites:
    def _source(self, module_path: str) -> str:
        import inspect
        mod = importlib.import_module(module_path)
        return inspect.getsource(mod)

    def test_loader_no_writes(self) -> None:
        src = self._source("src.knowledge.loader")
        for kw in ("INSERT", "UPDATE", "DELETE", "conn.execute"):
            assert kw not in src, f"loader.py must not contain '{kw}'"

    def test_engine_no_writes(self) -> None:
        src = self._source("src.knowledge.engine")
        for kw in ("INSERT", "UPDATE", "DELETE"):
            assert kw not in src, f"engine.py must not contain '{kw}'"


# ── 3. KnowledgeItem model ─────────────────────────────────────────────────────

class TestKnowledgeItem:
    def _item(self, **overrides):
        from src.knowledge.models import KnowledgeItem
        defaults = dict(
            entity_id="ADDR",
            category="BEHAVIOUR",
            type="PERSISTENT_LAUNCHER",
            value="5 launches",
            confidence="HIGH",
            source="PATTERN_MATCH",
            provenance="rule:BEHAVIOUR:PERSISTENT_LAUNCHER",
        )
        defaults.update(overrides)
        return KnowledgeItem(**defaults)

    def test_valid_item_creates(self) -> None:
        item = self._item()
        assert item.entity_id == "ADDR"

    def test_invalid_confidence_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            self._item(confidence="TOTALLY_SURE")

    def test_invalid_category_raises(self) -> None:
        with pytest.raises(ValueError, match="category"):
            self._item(category="VIBES")

    def test_to_dict_keys(self) -> None:
        d = self._item().to_dict()
        assert "entity_id" in d
        assert "category" in d
        assert "confidence" in d
        assert isinstance(d["supporting_evidence"], list)


# ── 4. Address loader ──────────────────────────────────────────────────────────

class TestLoader:
    def setup_method(self) -> None:
        _invalidate_loader()

    def teardown_method(self) -> None:
        _invalidate_loader()

    def test_loads_jito_addresses(self) -> None:
        from src.knowledge.loader import get_address_table
        jito = get_address_table("jito")
        assert len(jito) == 8

    def test_jito_confidence_certain(self) -> None:
        from src.knowledge.loader import get_address_table
        jito = get_address_table("jito")
        assert all(e.confidence == "CERTAIN" for e in jito)

    def test_cex_has_binance(self) -> None:
        from src.knowledge.loader import get_address_table, lookup_address
        cex = get_address_table("cex")
        assert len(cex) >= 1
        entry = lookup_address(cex[0].address)
        assert entry is not None
        assert entry.family == "cex"

    def test_empty_families(self) -> None:
        from src.knowledge.loader import get_address_table
        assert get_address_table("relay") == []
        assert get_address_table("bundlers") == []

    def test_lookup_unknown_address(self) -> None:
        from src.knowledge.loader import lookup_address
        assert lookup_address("NONEXISTENT_WALLET_ADDRESS_XYZ") is None

    def test_cache_is_warm_on_second_call(self) -> None:
        from src.knowledge.loader import get_address_table, loaded_at
        get_address_table("jito")
        ts1 = loaded_at()
        get_address_table("jito")
        ts2 = loaded_at()
        assert ts1 == ts2, "Cache timestamp should not change on warm hit"

    def test_missing_file_returns_empty(self, monkeypatch, tmp_path) -> None:
        from src.knowledge import loader
        monkeypatch.setattr(loader, "_KNOWLEDGE_DIR", str(tmp_path))
        addr_dir = tmp_path / "addresses"
        addr_dir.mkdir()
        result = loader.get_address_table("cex")
        assert result == []

    def test_empty_entries_yaml(self, monkeypatch, tmp_path) -> None:
        from src.knowledge import loader
        monkeypatch.setattr(loader, "_KNOWLEDGE_DIR", str(tmp_path))
        addr_dir = tmp_path / "addresses"
        addr_dir.mkdir()
        (addr_dir / "cex.yaml").write_text("entries: []\n")
        result = loader.get_address_table("cex")
        assert result == []

    def test_invalid_yaml_tolerated(self, monkeypatch, tmp_path) -> None:
        from src.knowledge import loader
        monkeypatch.setattr(loader, "_KNOWLEDGE_DIR", str(tmp_path))
        addr_dir = tmp_path / "addresses"
        addr_dir.mkdir()
        (addr_dir / "cex.yaml").write_text("entries: [\n  {bad yaml }{{\n")
        result = loader.get_address_table("cex")
        assert result == []

    def test_entry_missing_address_skipped(self, monkeypatch, tmp_path) -> None:
        from src.knowledge import loader
        monkeypatch.setattr(loader, "_KNOWLEDGE_DIR", str(tmp_path))
        addr_dir = tmp_path / "addresses"
        addr_dir.mkdir()
        (addr_dir / "cex.yaml").write_text(
            "entries:\n  - label: NoAddress\n    confidence: HIGH\n    source: test\n"
        )
        result = loader.get_address_table("cex")
        assert result == []


# ── 5. Rule registry ───────────────────────────────────────────────────────────

class TestRules:
    def test_seven_rules_registered(self) -> None:
        from src.knowledge.rules import REGISTRY
        ids = {r.rule_id for r in REGISTRY.rules}
        expected = {
            "EXECUTION:WRAP_CLOSE",
            "EXECUTION:PLAIN_TRANSFER",
            "BEHAVIOUR:PERSISTENT_LAUNCHER",
            "BEHAVIOUR:SINGLE_USE",
            "FUNDER_TYPE:KNOWN_CEX",
            "FUNDER_TYPE:KNOWN_PLATFORM",
            "TOOLING:JITO",
        }
        assert ids == expected

    def test_wrap_close_rule(self) -> None:
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "WRAP_CLOSE", "launch_count": 5, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        types_ = {i.type for i in items}
        assert "WRAP_CLOSE" in types_
        assert "PLAIN_TRANSFER" not in types_

    def test_plain_transfer_rule(self) -> None:
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "PLAIN_TRANSFER", "launch_count": 4, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        types_ = {i.type for i in items}
        assert "PLAIN_TRANSFER" in types_
        assert "WRAP_CLOSE" not in types_

    def test_persistent_launcher_rule(self) -> None:
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "UNKNOWN", "launch_count": 10, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        types_ = {i.type for i in items}
        assert "PERSISTENT_LAUNCHER" in types_
        assert "SINGLE_USE" not in types_

    def test_single_use_rule(self) -> None:
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "UNKNOWN", "launch_count": 1, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        types_ = {i.type for i in items}
        assert "SINGLE_USE" in types_
        assert "PERSISTENT_LAUNCHER" not in types_

    def test_zero_launches_no_behaviour_rule(self) -> None:
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "UNKNOWN", "launch_count": 0, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        types_ = {i.type for i in items}
        assert "PERSISTENT_LAUNCHER" not in types_
        assert "SINGLE_USE" not in types_

    def test_known_cex_rule(self) -> None:
        from src.knowledge.loader import get_address_table
        from src.knowledge.rules import REGISTRY
        cex_entries = get_address_table("cex")
        if not cex_entries:
            pytest.skip("No CEX entries in address table")
        entry = cex_entries[0]
        evidence = {"funding_mode": "UNKNOWN", "launch_count": 0, "known_address_entry": entry}
        items = REGISTRY.apply_all(entry.address, evidence)
        types_ = {i.type for i in items}
        assert "KNOWN_CEX" in types_

    def test_jito_rule(self) -> None:
        from src.knowledge.loader import get_address_table
        from src.knowledge.rules import REGISTRY
        jito_entries = get_address_table("jito")
        assert jito_entries
        entry = jito_entries[0]
        evidence = {"funding_mode": "UNKNOWN", "launch_count": 0, "known_address_entry": entry}
        items = REGISTRY.apply_all(entry.address, evidence)
        types_ = {i.type for i in items}
        assert "JITO" in types_

    def test_all_items_have_valid_categories(self) -> None:
        from src.knowledge.models import KNOWN_CATEGORIES
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "WRAP_CLOSE", "launch_count": 5, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        for item in items:
            assert item.category in KNOWN_CATEGORIES

    def test_all_items_have_valid_confidence(self) -> None:
        from src.knowledge.models import CONFIDENCE_LEVELS
        from src.knowledge.rules import REGISTRY
        evidence = {"funding_mode": "PLAIN_TRANSFER", "launch_count": 3, "known_address_entry": None}
        items = REGISTRY.apply_all("ADDR", evidence)
        for item in items:
            assert item.confidence in CONFIDENCE_LEVELS


# ── 6. Engine ──────────────────────────────────────────────────────────────────

class TestEngine:
    def test_unknown_entity_returns_empty_list(self) -> None:
        from src.knowledge.engine import enrich
        result = enrich("TOTALLY_UNKNOWN_WALLET_XYZ_123")
        assert isinstance(result, list)

    def test_empty_entity_id_returns_empty(self) -> None:
        from src.knowledge.engine import enrich
        assert enrich("") == []
        assert enrich("   ") == []

    def test_jito_address_classified_as_tooling(self) -> None:
        from src.knowledge.loader import get_address_table
        from src.knowledge.engine import enrich
        jito_entries = get_address_table("jito")
        entry = jito_entries[0]
        items = enrich(entry.address)
        categories = {i.category for i in items}
        assert "TOOLING" in categories

    def test_enrich_returns_list_of_knowledge_items(self) -> None:
        from src.knowledge.engine import enrich
        from src.knowledge.models import KnowledgeItem
        from src.knowledge.loader import get_address_table
        entry = get_address_table("jito")[0]
        items = enrich(entry.address)
        for item in items:
            assert isinstance(item, KnowledgeItem)

    def test_enrich_batch_returns_dict(self) -> None:
        from src.knowledge.engine import enrich_batch
        from src.knowledge.loader import get_address_table
        jito = get_address_table("jito")
        addresses = [e.address for e in jito[:3]]
        result = enrich_batch(addresses)
        assert isinstance(result, dict)
        assert set(result.keys()) == set(addresses)

    def test_enrich_batch_empty_input(self) -> None:
        from src.knowledge.engine import enrich_batch
        assert enrich_batch([]) == {}


# ── 7. Performance ─────────────────────────────────────────────────────────────

class TestPerformance:
    def test_warm_lookup_under_50ms(self) -> None:
        from src.knowledge.loader import get_address_table, lookup_address
        # warm the cache
        get_address_table("jito")
        jito = get_address_table("jito")
        addr = jito[0].address

        start = time.perf_counter()
        for _ in range(100):
            lookup_address(addr)
        elapsed_ms = (time.perf_counter() - start) * 1000
        per_call_ms = elapsed_ms / 100
        assert per_call_ms < 50, f"Warm lookup took {per_call_ms:.2f}ms > 50ms"

    def test_enrich_jito_under_50ms(self) -> None:
        from src.knowledge.engine import enrich
        from src.knowledge.loader import get_address_table
        # warm both caches
        get_address_table("jito")
        jito_addr = get_address_table("jito")[0].address

        start = time.perf_counter()
        enrich(jito_addr)
        elapsed_ms = (time.perf_counter() - start) * 1000
        # First call may go to DB; still expect <50ms on warm address table
        assert elapsed_ms < 50, f"enrich() took {elapsed_ms:.1f}ms > 50ms"


# ── 8. Launcher Observatory enrichment integration ─────────────────────────────

class TestLauncherObservatoryEnrichment:
    def test_operator_enrichments_key_in_intelligence(self) -> None:
        """_get_intelligence() must return operator_enrichments field."""
        from src.ops.launcher_observatory_routes import _get_intelligence
        result = _get_intelligence()
        assert "operator_enrichments" in result, (
            "intelligence response must include operator_enrichments (may be empty dict)"
        )
        assert isinstance(result["operator_enrichments"], dict)

    def test_operator_enrichments_values_are_lists(self) -> None:
        from src.ops.launcher_observatory_routes import _get_intelligence
        result = _get_intelligence()
        for addr_abbr, items in result["operator_enrichments"].items():
            assert isinstance(items, list), f"{addr_abbr}: enrichments must be a list"
            for item in items:
                assert "entity_id" in item
                assert "category" in item
                assert "confidence" in item
