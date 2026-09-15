"""Public graph read errors shared by readers and maintenance."""


class GraphReadError(ValueError):
    """A graph read is out of scope, unsupported or exceeds a safe bound."""
