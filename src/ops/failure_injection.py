"""Generic inert durability-boundary failure seam."""
VERSION="DURABILITY_FAILURE_INJECTOR_V1"
class InjectedFailure(RuntimeError): pass
class FailureInjector:
 def hit(self,boundary_id): return None
class DeterministicFailureInjector(FailureInjector):
 def __init__(self,*boundaries): self.boundaries=set(boundaries);self.hits=[]
 def hit(self,boundary_id):
  self.hits.append(boundary_id)
  if boundary_id in self.boundaries: raise InjectedFailure(boundary_id)
