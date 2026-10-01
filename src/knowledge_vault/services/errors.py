class KnowledgeVaultError(Exception):
    code = "knowledge_vault_error"
    status_code = 400


class NotFoundError(KnowledgeVaultError):
    code = "not_found"
    status_code = 404


class ConflictError(KnowledgeVaultError):
    code = "conflict"
    status_code = 409


class BatchIncompleteError(ConflictError):
    code = "batch_incomplete"


class UnauthorizedError(KnowledgeVaultError):
    code = "unauthorized"
    status_code = 401


class ForbiddenError(KnowledgeVaultError):
    code = "forbidden"
    status_code = 403


class RateLimitedError(KnowledgeVaultError):
    code = "rate_limited"
    status_code = 429
