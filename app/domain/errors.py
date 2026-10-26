class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def missing(resource: str, identifier):
    return DomainError(
        "NOT_FOUND", "Resource not found", 404, resource=resource, id=str(identifier)
    )
