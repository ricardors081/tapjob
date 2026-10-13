"""Domain exception taxonomy for Jobs and Manual Intake (WP2.1-A)."""


class JobDomainError(Exception):
    """Base exception for all jobs domain operations."""


class JobTextTooShortError(JobDomainError):
    """Raised when job description text does not meet the minimum length threshold."""


class JobTextTooLongError(JobDomainError):
    """Raised when job description text exceeds the maximum allowable length."""


class JobUrlInvalidError(JobDomainError):
    """Raised when an intake URL fails parsing or contains credentials."""


class JobUrlDestinationForbiddenError(JobDomainError):
    """Raised when DNS resolution resolves to private, loopback, or forbidden IP space."""


class JobUrlUnreachableError(JobDomainError):
    """Raised when connection cannot be established or host is unreachable."""


class JobUrlTlsFailedError(JobDomainError):
    """Raised when TLS handshake or certificate hostname validation fails."""


class JobUrlTooLargeError(JobDomainError):
    """Raised when streamed decoded response bytes exceed the 2 MiB safety bound."""


class JobContentTypeUnsupportedError(JobDomainError):
    """Raised when response Content-Type is not supported (e.g. binary, PDF, media)."""


class JobPageRequiresManualPasteError(JobDomainError):
    """Raised when HTML page depends on dynamic JavaScript and contains no extractable body."""


class JobNotFoundError(JobDomainError):
    """Raised when a requested Job entity cannot be found by ID."""


class CompanyNotFoundError(JobDomainError):
    """Raised when a requested Company entity cannot be found by ID."""


class IdempotencyKeyPayloadMismatchError(JobDomainError):
    """Raised when an Idempotency-Key is reused with a different request fingerprint."""


class ConcurrentRequestInProgressError(JobDomainError):
    """Raised when a concurrent request with the same Idempotency-Key is already in PENDING."""


class IdempotencyOwnershipLostError(JobDomainError):
    """Raised when attempting to complete an idempotency key whose reservation was overtaken."""
