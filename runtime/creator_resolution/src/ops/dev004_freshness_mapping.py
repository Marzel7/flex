"""Explicit, bounded DEV004 seed-to-forward freshness metadata. No schema or providers."""
from __future__ import annotations
import hashlib, json, sqlite3
from pathlib import Path

FAMILIES = {"queue": "wt_walkback_queue", "edges": "wt_walkback_edge_candidates", "atomic": "wt_walkback_atomic_flows"}
CANDIDATES = {"p3r-v2-c357da9d0d4d560311e4", "p3r-v2-dc4953db7adb853337c4"}
MAX_MAPPING_BYTES = 500000
MAX_ROWS = 2048
LIMIT = 10**12 - 1

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def generation(value):
    parts = [int(x) for x in value.split(":")]
    if len(parts) != 3 or any(x < 0 or x > LIMIT for x in parts):
        raise ValueError("FRESHNESS_GENERATION_INVALID")
    return dict(zip(FAMILIES, parts))

def read_rows(conn, spec):
    cfg = spec["persisted_source"]
    if cfg["highwater_tables"] != FAMILIES or cfg["evidence_tables"] != FAMILIES:
        raise ValueError("FRESHNESS_SOURCE_FAMILY_MISMATCH")
    mints = [x[0] for x in conn.execute("SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint LIMIT 129", (cfg["run_id"], spec["source_candidate_id"]))]
    if not mints or len(mints) == 129:
        raise ValueError("FRESHNESS_MEMBERSHIP_MISSING_OR_OVERFLOW")
    marks = {k: conn.execute("SELECT COALESCE(MAX(rowid),0) FROM " + t).fetchone()[0] for k, t in FAMILIES.items()}
    rows = {}
    for family, table in FAMILIES.items():
        columns = [r[1] for r in conn.execute("PRAGMA table_info(" + table + ")") if r[5]]
        if len(columns) != 1:
            raise ValueError("FRESHNESS_IDENTITY_CONTRACT_MISSING")
        key = columns[0]
        cursor = conn.execute("SELECT rowid AS __position,* FROM " + table + " WHERE mint IN (" + ",".join("?" * len(mints)) + ") LIMIT ?", (*mints, MAX_ROWS + 1))
        names = [d[0] for d in cursor.description]
        values = [dict(zip(names, r)) for r in cursor]
        if len(values) > MAX_ROWS:
            raise ValueError("FRESHNESS_SOURCE_ROWS_OVERFLOW")
        rows[family] = [{"rowid": x.pop("__position"), "identity": str(x[key]), "mint": x["mint"], "funder_wallet": x.get("funder_wallet"), "content": digest(x)} for x in values]
    return mints, marks, rows

def build_mapping(path, registry, *, export_generation=None):
    """Read the immutable seed only; no cursor/current row is changed or consumed."""
    if {s["source_candidate_id"] for s in registry.values()} != CANDIDATES:
        raise ValueError("FRESHNESS_CANDIDATE_SCOPE")
    conn = sqlite3.connect("file:" + str(Path(path).resolve()) + "?mode=ro", uri=True)
    try:
        conn.execute("BEGIN")
        exported = generation(export_generation) if export_generation is not None else None
        out = {"contract": "dev004-forward-freshness.v1", "candidates": {}}
        for spec in registry.values():
            owner = spec["potential_operation_id"]
            current = conn.execute("SELECT assessment_id,freshness_key FROM potential_operation_current WHERE potential_operation_id=?", (owner,)).fetchone()
            if not current:
                raise ValueError("FRESHNESS_BASE_ASSESSMENT_REQUIRED")
            mints, marks, rows = read_rows(conn, spec)
            out["candidates"][owner] = {"source_candidate_id": spec["source_candidate_id"], "base_assessment_id": current[0], "base_generation": current[1], "assessment_origin": generation(current[1]), "export_generation_floor": exported or generation(current[1]), "logical_origin": {k: max(generation(current[1])[k], (exported or generation(current[1]))[k]) for k in FAMILIES}, "physical_origin": marks, "membership_digest": digest(mints), "inherited_memberships": mints, "inherited_identities": {k: {x["identity"]: x["content"] for x in values} for k, values in rows.items()}}
        out["mapping_id"] = digest(out)
        encoded = json.dumps(out, sort_keys=True).encode()
        if len(encoded) > MAX_MAPPING_BYTES:
            raise ValueError("FRESHNESS_MAPPING_OVERFLOW")
        return out
    finally:
        conn.close()

def load_mapping(path):
    with open(path, "rb") as f:
        data = f.read(MAX_MAPPING_BYTES + 1)
    if len(data) > MAX_MAPPING_BYTES:
        raise ValueError("FRESHNESS_MAPPING_OVERFLOW")
    result = json.loads(data)
    if result.get("contract") != "dev004-forward-freshness.v1":
        raise ValueError("FRESHNESS_MAPPING_CONTRACT")
    identity = result.get("mapping_id")
    if identity != digest({k: v for k, v in result.items() if k != "mapping_id"}):
        raise ValueError("FRESHNESS_MAPPING_DIGEST")
    return result

def bind_mapping(spec, mapping):
    record = mapping["candidates"].get(spec["potential_operation_id"])
    if not record or record["source_candidate_id"] != spec["source_candidate_id"] or spec["source_candidate_id"] not in CANDIDATES:
        raise ValueError("FRESHNESS_CANDIDATE_BINDING")
    return {**spec, "persisted_source": {**spec["persisted_source"], "generation_mapping": record}, "historical_inherited_context": [{"kind": "IMMUTABLE_BASE_ASSESSMENT_REFERENCE", "assessment_id": record["base_assessment_id"], "generation": record["base_generation"]}]}

def mapped_context(conn, spec, event):
    record = spec["persisted_source"]["generation_mapping"]
    base = generation(record["base_generation"])
    if record["assessment_origin"] != base or record["logical_origin"] != {k: max(base[k], record["export_generation_floor"][k]) for k in FAMILIES}:
        raise ValueError("FRESHNESS_LOGICAL_ORIGIN_MISMATCH")
    logical = record["logical_origin"]
    historical = conn.execute("SELECT potential_operation_id,freshness_key FROM potential_operation_assessment_version WHERE assessment_id=?", (record["base_assessment_id"],)).fetchone()
    if historical != (spec["potential_operation_id"], record["base_generation"]):
        raise ValueError("FRESHNESS_BASE_ASSESSMENT_MISMATCH")
    mints, physical, rows = read_rows(conn, spec)
    inherited_mints = record["inherited_memberships"]
    if digest(inherited_mints) != record["membership_digest"] or not set(inherited_mints).issubset(mints):
        raise ValueError("FRESHNESS_MEMBERSHIP_CHANGED")
    prior = generation(event["current_generation"])
    if any(prior[k] < base[k] for k in FAMILIES):
        raise ValueError("FRESHNESS_CURSOR_BEFORE_BASE")
    marks = {}; newer = False
    for family in FAMILIES:
        origin = record["physical_origin"][family]
        if not isinstance(origin, int) or origin < 0 or physical[family] < origin:
            raise ValueError("FRESHNESS_PHYSICAL_ORIGIN_INVALID")
        marks[family] = logical[family] + physical[family] - origin
        if marks[family] > LIMIT or marks[family] < prior[family]:
            raise ValueError("FRESHNESS_GENERATION_RANGE_OR_REGRESSION")
        inherited = record["inherited_identities"][family]
        for row in rows[family]:
            if row["rowid"] <= origin:
                # Inherited seed context is not asserted to have been consumed
                # by the old assessment. It cannot itself be a new V2 admission.
                if row["identity"] not in inherited:
                    raise ValueError("FRESHNESS_UNMAPPED_BASE_IDENTITY")
                continue
            if row["identity"] in inherited:
                if row["content"] != inherited[row["identity"]]:
                    raise ValueError("FRESHNESS_HISTORICAL_IDENTITY_CONFLICT")
                continue
            newer |= logical[family] + row["rowid"] - origin > prior[family]
    relevant = newer and (event.get("mint") in set(mints) if event.get("mint") else True)
    return {"members": mints, "funders": sorted({x["funder_wallet"] for x in rows["queue"] if x["funder_wallet"]}), "highwaters": marks, "source_rows": {k: len(v) for k, v in rows.items()}, "global_advanced": any(marks[k] > prior[k] for k in FAMILIES), "relevant_new_evidence": relevant, "association_ids": []}
