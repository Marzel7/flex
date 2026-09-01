#!/usr/bin/env python3
"""PSI0H-H4R bounded historical continuity review packet runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence.contracts.psi0h_h4r_historical_continuity_review_packet import (
    Psi0hH4RHistoricalContinuityReviewPacketError,
    qualify_historical_continuity_review_packet,
    verify_historical_continuity_review_packet,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, h2_artifact: str, h3_artifact: str, h4_artifact: str, output: str | None = None) -> dict:
    h2_path = Path(h2_artifact)
    h3_path = Path(h3_artifact)
    h4_path = Path(h4_artifact)
    if not h2_path.is_file():
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H2_ARTIFACT_MISSING")
    if not h3_path.is_file():
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H3_ARTIFACT_MISSING")
    if not h4_path.is_file():
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_ARTIFACT_MISSING")

    h2_data = _read_json(h2_path)
    h3_data = _read_json(h3_path)
    h4_data = _read_json(h4_path)

    packet = qualify_historical_continuity_review_packet(
        h2_artifact=h2_data,
        h3_artifact=h3_data,
        h4_artifact=h4_data,
    )
    verify_historical_continuity_review_packet(packet)

    output_path = Path(output or "docs/audits/psi0h_h4r_historical_continuity_review_packet.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    index_path = output_path.with_name("psi0h_h4r_historical_continuity_review_index.json")
    index_payload = {
        "schema_version": "psi0h-h4r.historical-continuity-review-index.v1",
        "milestone": "PSI0H-H4R",
        "status": packet["status"],
        "candidate_count": packet["candidate_count"],
        "review_index": packet["review_index"],
        "source": {
            "packet_artifact": output_path.as_posix(),
            "packet_digest": packet["artifact_digest"],
        },
    }
    index_payload["artifact_digest"] = (
        __import__("hashlib")
        .sha256(json.dumps(index_payload, sort_keys=True, separators=(",", ":")).encode())
        .hexdigest()
    )
    index_path.write_text(json.dumps(index_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    return {
        "artifact": output_path.as_posix(),
        "index_artifact": index_path.as_posix(),
        "artifact_digest": packet["artifact_digest"],
        "index_artifact_digest": index_payload["artifact_digest"],
        "status": packet["status"],
        "verdict": packet["verdict"],
        "candidate_count": packet["candidate_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PSI0H-H4R historical continuity review packet.")
    parser.add_argument("--h2-artifact", required=True, help="Path to PSI0H-H2 artifact JSON.")
    parser.add_argument("--h3-artifact", required=True, help="Path to PSI0H-H3 artifact JSON.")
    parser.add_argument("--h4-artifact", required=True, help="Path to PSI0H-H4 artifact JSON.")
    parser.add_argument(
        "--output",
        default="docs/audits/psi0h_h4r_historical_continuity_review_packet.json",
        help="Output packet path. Index payload is written as ..._index.json beside it.",
    )
    args = parser.parse_args()

    try:
        result = run(
            h2_artifact=args.h2_artifact,
            h3_artifact=args.h3_artifact,
            h4_artifact=args.h4_artifact,
            output=args.output,
        )
    except Psi0hH4RHistoricalContinuityReviewPacketError as exc:
        raise SystemExit(f"{exc}") from exc
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
