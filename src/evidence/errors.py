class EvidenceError(RuntimeError):
    """Base class for isolated Evidence Platform failures."""


class ComponentDisabled(EvidenceError):
    pass


class IsolationError(EvidenceError):
    pass


class QueueFull(EvidenceError):
    pass


class QueueCorruption(EvidenceError):
    pass


class ArtifactCorruption(EvidenceError):
    pass


class ArtifactConflict(EvidenceError):
    pass


class WriterOwnershipError(EvidenceError):
    pass
