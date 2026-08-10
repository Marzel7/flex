def test_score_creator_releases_setup_write_before_context_reads(monkeypatch):
    from src.core import risk_scoring_builder as module

    events = []

    class _Connection:
        row_factory = None

        def execute(self, sql, params=()):
            events.append(("execute", sql.strip().split()[0].upper()))
            return self

        def commit(self):
            events.append(("commit", None))

        def rollback(self):
            events.append(("rollback", None))

        def close(self):
            events.append(("close", None))

    connections = [_Connection(), _Connection()]
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args, **kwargs: connections.pop(0))

    builder = module.RiskScoringBuilder("unused.db")
    monkeypatch.setattr(builder, "apply_migration", lambda conn: events.append(("migration", None)))
    monkeypatch.setattr(module, "ensure_infra_wallets_table", lambda conn: events.append(("infra", None)))

    def context(conn, creator):
        assert ("commit", None) in events
        events.append(("context", creator))
        return {}

    monkeypatch.setattr(builder, "_build_context_for_creator", context)
    monkeypatch.setattr(builder, "_score_creator_fast", lambda creator, ctx: {"creator_address": creator, "risk_level": "LOW"})
    monkeypatch.setattr(builder, "_write_creator_scores", lambda conn, rows: events.append(("write", len(rows))))

    result = builder.score_creator_now("creator")

    assert result["status"] == "success"
    assert events.index(("commit", None)) < events.index(("context", "creator"))
