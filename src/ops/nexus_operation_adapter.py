"""Read-only Nexus adapter over already-retained transaction-role evidence."""
from __future__ import annotations
import hashlib,json
from src.ops.direct_10k_creator_provisioning import DETECTOR_ID,detect_direct_10k_creator_provisioning
from src.ops.operation_fact_framework import EvidenceCompleteness,OperationContract,OperationFact
from src.ops.actionability_evidence import build_actionability_facts
NEXUS_ID='bd7d7479-1454-5d41-9f68-115550348f3e'
class NexusOperationAdapter:
 def build_facts(self,evidence:dict,contract:OperationContract)->list[OperationFact]:
  result=detect_direct_10k_creator_provisioning(evidence)
  complete=EvidenceCompleteness.COMPLETE if result['result'] not in {'INSUFFICIENT_INPUT','AMBIGUOUS'} else EvidenceCompleteness.PARTIAL
  provenance={'defining_signature':evidence.get('defining_signature'),'role_digest':evidence.get('provenance_digest')}
  payload={'detector_id':DETECTOR_ID,'result':result['result'],'reason_code':result['reason'],'defining_signature':evidence.get('defining_signature')}
  return [OperationFact(NEXUS_ID,evidence['mint'],'DETECTOR_RESULT','v1',contract.version,payload,provenance,complete),OperationFact(NEXUS_ID,evidence['mint'],'EVIDENCE_COMPLETENESS','v1',contract.version,{'state':complete.value},provenance,complete)]

class NexusProspectiveActionabilityShadowAdapter:
 """Disabled shadow adapter; evidence must already be captured and decoded."""
 live_capture_enabled = False
 def build_facts(self,evidence:dict,contract:OperationContract)->list[OperationFact]:
  return build_actionability_facts(evidence,contract)
