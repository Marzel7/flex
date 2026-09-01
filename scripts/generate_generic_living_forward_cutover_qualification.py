#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.ops.generic_living_forward_cutover import qualify_forward_cutover

def main():
 report=qualify_forward_cutover()
 report.update({'schema_version':'generic_living_forward_cutover_qualification.v1','historical_source_limitation':'Missing aggregate categories have no independently persisted same-boundary underlying evidence; association_inputs were not reused as source evidence.','historical_context_model':'HISTORICAL_INHERITED_CONTEXT is payload context only, never a newly-derived association.','ui_history_projection':{'legacy_and_generic_versions_visible':True,'current_generic_visible':True,'promotion':'NO','detector_activation':'NO','status':'PAUSED','pipeline_lineage_visible':True},'tests':'python -m pytest -q tests/test_generic_living_pipeline_v2.py','cutover_readiness':'HOLD_FORWARD_GENERIC_CUTOVER','verdict':'FORWARD_CUTOVER_REQUIRES_ADDITIVE_LINEAGE_METADATA','exact_next_step':'Qualify a real-schema additive assessment-association lineage migration and read-model projection before any active-path cutover.'})
 encoded=json.dumps(report,indent=2,sort_keys=True)+'\n'; out=Path('docs/audits/generic_living_forward_cutover_qualification.v1.json'); out.write_text(encoded); print(hashlib.sha256(encoded.encode()).hexdigest())
if __name__=='__main__': main()
