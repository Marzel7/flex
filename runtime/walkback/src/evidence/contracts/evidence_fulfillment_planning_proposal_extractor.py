"""EB1.3G fixture-only query extractor for non-executable proposals."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Optional
from urllib.parse import quote

from .evidence_fulfillment_planning_proposal import AUTHORITY
from .evidence_fulfillment_planning_proposal_adapters import (
    _load_verified_bundle,
    adapt_verified_lineage_to_planning_proposal_projection,
)
from .evidence_fulfillment_planning_proposal_corpus import (
    EvidenceFulfillmentPlanningProposalCorpus,
    assemble_evidence_fulfillment_planning_proposal_corpus,
    verify_evidence_fulfillment_planning_proposal_corpus,
)
from .evidence_fulfillment_planning_proposal_manifest import (
    EvidenceFulfillmentPlanningProposalManifest,
    build_evidence_fulfillment_planning_proposal_manifest,
    verify_evidence_fulfillment_planning_proposal_manifest,
)
from .requirement_review_disposition import project_requirement_review_history

SCHEMA_VERSION = "eb1.3g.v1"
MAX_SECONDS = 30.0
MAX_REQUIREMENTS = 8
MAX_REVIEW_ROWS = 64
MAX_PROPOSAL_ROWS = 64
MAX_SELECTED_PROPOSALS = 64
MAX_JSON_BYTES = 262_144
MAX_BUNDLE_BYTES = 1_048_576
MAX_CORPUS_LANES = 8
TABLES = ("review_records", "proposal_records")


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalExtractionAccounting:
    input_requirement_count: int
    review_record_count: int
    proposal_input_count: int
    selected_proposal_count: int
    excluded_not_ready_count: int
    unknown_requirement_count: int
    conflict_count: int
    accounting_residual: int


class EvidenceFulfillmentPlanningProposalExtractorError(RuntimeError):
    def __init__(self, code, accounting=None):
        super().__init__(code)
        self.accounting = accounting


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalExtraction:
    schema_version: str
    status: str
    input_fingerprint: str
    eb1_1h_bundle_digest: str
    accounting: EvidenceFulfillmentPlanningProposalExtractionAccounting
    manifest: Optional[EvidenceFulfillmentPlanningProposalManifest]
    corpus: Optional[EvidenceFulfillmentPlanningProposalCorpus]
    authority_class: str
    grants_planning_authority: bool
    grants_execution_authority: bool
    result_digest: str


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _schema_matches(connection, table):
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    actual = tuple((row[1], row[2].upper(), row[3], row[4], row[5]) for row in rows)
    return actual == (
        ("position", "INTEGER", 1, None, 1),
        ("canonical_json", "TEXT", 1, None, 0),
    )


def _parse_rows(rows, table):
    records = []
    for expected_position, row in enumerate(rows):
        if row["position"] != expected_position:
            raise EvidenceFulfillmentPlanningProposalExtractorError(
                f"EB1_3G_{table.upper()}_POSITION_DRIFT"
            )
        raw = row["canonical_json"]
        if not isinstance(raw, str):
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_INVALID_JSON")
        try:
            value = json.loads(raw)
        except Exception as exc:
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_INVALID_JSON") from exc
        if json.dumps(value, sort_keys=True, separators=(",", ":")) != raw:
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_NONCANONICAL_JSON")
        records.append(value)
    return records


def extract_evidence_fulfillment_planning_proposals(
    bundle_directory: Path,
    sqlite_path: Path,
    *,
    max_query_seconds=MAX_SECONDS,
    clock=time.monotonic,
):
    if max_query_seconds <= 0 or max_query_seconds > MAX_SECONDS:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_INVALID_QUERY_BOUND")
    bundle_directory = Path(bundle_directory)
    if not bundle_directory.is_dir():
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_BUNDLE_NOT_FOUND")
    bundle_bytes = sum(item.stat().st_size for item in bundle_directory.iterdir() if item.is_file())
    if bundle_bytes > MAX_BUNDLE_BYTES:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_BUNDLE_LIMIT_EXCEEDED")
    try:
        verified_bundle, _, _, requirements = _load_verified_bundle(bundle_directory)
    except Exception as exc:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_UNVERIFIED_BUNDLE") from exc
    if not requirements or len(requirements) > MAX_REQUIREMENTS:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_REQUIREMENT_LIMIT_EXCEEDED")
    sqlite_path = Path(sqlite_path)
    if not sqlite_path.is_file():
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_SOURCE_NOT_FOUND")
    deadline = clock() + max_query_seconds
    connection = sqlite3.connect(
        f"file:{quote(str(sqlite_path.resolve()), safe='/')}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_QUERY_ONLY_NOT_ENFORCED")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(TABLES) or any(not _schema_matches(connection, table) for table in TABLES):
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_SCHEMA_MISMATCH")
        connection.set_progress_handler(lambda: int(clock() >= deadline), 1000)
        review_rows = connection.execute(
            "SELECT position,canonical_json FROM review_records ORDER BY position"
        ).fetchall()
        proposal_rows = connection.execute(
            "SELECT position,canonical_json FROM proposal_records ORDER BY position"
        ).fetchall()
        if clock() >= deadline:
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_QUERY_TIMEOUT")
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower():
            raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_QUERY_TIMEOUT") from exc
        raise
    finally:
        connection.close()
    if not review_rows or len(review_rows) > MAX_REVIEW_ROWS or len(proposal_rows) > MAX_PROPOSAL_ROWS:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_ROW_LIMIT_EXCEEDED")
    total_bytes = sum(len(row["canonical_json"].encode()) for row in (*review_rows, *proposal_rows))
    if total_bytes > MAX_JSON_BYTES:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_BYTE_LIMIT_EXCEEDED")
    reviews = _parse_rows(review_rows, "review")
    proposals = _parse_rows(proposal_rows, "proposal")
    base = dict(
        input_requirement_count=len(requirements),
        review_record_count=len(reviews),
        proposal_input_count=len(proposals),
    )
    try:
        review_history = project_requirement_review_history(reviews, requirements)
    except Exception as exc:
        accounting = EvidenceFulfillmentPlanningProposalExtractionAccounting(
            **base, selected_proposal_count=0, excluded_not_ready_count=0,
            unknown_requirement_count=0, conflict_count=1,
            accounting_residual=len(proposals),
        )
        raise EvidenceFulfillmentPlanningProposalExtractorError(
            "EB1_3G_REVIEW_CONFLICT", accounting
        ) from exc
    requirement_ids = {item.requirement_id for item in requirements}
    latest = {}
    for disposition in review_history.dispositions:
        current = latest.get(disposition.requirement_id)
        if current is None or disposition.review_sequence > current.review_sequence:
            latest[disposition.requirement_id] = disposition
    selected = []
    excluded_not_ready = 0
    unknown = 0
    for proposal in proposals:
        requirement_id = proposal.get("requirement_id") if isinstance(proposal, dict) else None
        if requirement_id not in requirement_ids:
            unknown += 1
        elif requirement_id not in latest or latest[requirement_id].disposition != "READY_FOR_SEPARATE_PLANNING":
            excluded_not_ready += 1
        else:
            selected.append(proposal)
    residual = len(proposals) - len(selected) - excluded_not_ready - unknown
    accounting = EvidenceFulfillmentPlanningProposalExtractionAccounting(
        **base,
        selected_proposal_count=len(selected),
        excluded_not_ready_count=excluded_not_ready,
        unknown_requirement_count=unknown,
        conflict_count=0,
        accounting_residual=residual,
    )
    if unknown or residual:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_ACCOUNTING_REJECTED", accounting)
    input_fingerprint = _digest(
        {"bundle_digest": verified_bundle.bundle_digest, "reviews": reviews, "proposals": proposals}
    )
    if not selected:
        body = {
            "schema_version": SCHEMA_VERSION,
            "status": "NO_ELIGIBLE_PROPOSALS",
            "input_fingerprint": input_fingerprint,
            "bundle_digest": verified_bundle.bundle_digest,
            "accounting": asdict(accounting),
            "authority_class": AUTHORITY,
            "grants_planning_authority": False,
            "grants_execution_authority": False,
        }
        return EvidenceFulfillmentPlanningProposalExtraction(
            SCHEMA_VERSION, "NO_ELIGIBLE_PROPOSALS", input_fingerprint,
            verified_bundle.bundle_digest, accounting, None, None, AUTHORITY, False, False,
            _digest(body),
        )
    if len(selected) > MAX_SELECTED_PROPOSALS:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_PROPOSAL_LIMIT_EXCEEDED", accounting)
    try:
        projection = adapt_verified_lineage_to_planning_proposal_projection(
            bundle_directory, review_history, selected
        )
        manifest = build_evidence_fulfillment_planning_proposal_manifest(projection)
        verify_evidence_fulfillment_planning_proposal_manifest(manifest, projection)
        corpus = assemble_evidence_fulfillment_planning_proposal_corpus([(manifest, projection)])
        verify_evidence_fulfillment_planning_proposal_corpus(corpus, [(manifest, projection)])
    except Exception as exc:
        failed = EvidenceFulfillmentPlanningProposalExtractionAccounting(
            **{**asdict(accounting), "conflict_count": 1}
        )
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_PROJECTION_REPLAY_REJECTED", failed) from exc
    if len(corpus.lanes) > MAX_CORPUS_LANES:
        raise EvidenceFulfillmentPlanningProposalExtractorError("EB1_3G_LANE_LIMIT_EXCEEDED", accounting)
    body = {
        "schema_version": SCHEMA_VERSION,
        "status": "PROJECTED",
        "input_fingerprint": input_fingerprint,
        "bundle_digest": verified_bundle.bundle_digest,
        "accounting": asdict(accounting),
        "manifest_digest": manifest.manifest_digest,
        "corpus_digest": corpus.corpus_digest,
        "authority_class": AUTHORITY,
        "grants_planning_authority": False,
        "grants_execution_authority": False,
    }
    return EvidenceFulfillmentPlanningProposalExtraction(
        SCHEMA_VERSION, "PROJECTED", input_fingerprint, verified_bundle.bundle_digest,
        accounting, manifest, corpus, AUTHORITY, False, False, _digest(body),
    )
