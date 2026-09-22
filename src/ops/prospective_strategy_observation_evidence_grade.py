"""Operation-independent evidence grades; weaker inputs cap result claims."""
from dataclasses import dataclass
ORDER={'UNQUALIFIED':0,'MARK_ONLY':1,'PARTIAL_EVENT_STATE':2,'QUALIFIED_EXACT_STATE':3}
@dataclass(frozen=True)
class ObservationEvidenceGrade:
 trigger:str; entry:str; lifecycle:str; exit:str
 def result(self): return min((self.trigger,self.entry,self.lifecycle,self.exit),key=lambda x:ORDER[x])
 def supports_exact_execution(self): return self.result()=='QUALIFIED_EXACT_STATE'
 def supports_cross_candidate_comparison(self): return self.trigger!='UNQUALIFIED' and self.entry==self.exit and ORDER[self.entry]>=2
def submission_capability(): return 'NONE'
