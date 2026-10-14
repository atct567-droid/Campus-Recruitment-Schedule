class ApiError(RuntimeError):
    """A failure that must never be converted to a successful empty result."""

    def __init__(self, kind: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": str(self), "http_status": self.status}
