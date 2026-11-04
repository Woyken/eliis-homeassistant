"""Exception hierarchy for the Eliis API client.

Errors are intentionally categorical. Callers map categories to Home
Assistant behaviour; exception messages never contain credentials, raw
response bodies, private identifiers or request URLs.
"""

from __future__ import annotations


class EliisError(Exception):
    """Base class for all Eliis client errors."""


class EliisAuthError(EliisError):
    """The account is not authenticated, or the challenge was rejected."""


class EliisPermissionError(EliisError):
    """The account is authenticated but not allowed to read this resource."""


class EliisConnectionError(EliisError):
    """A transient network, timeout or upstream server failure."""


class EliisResponseError(EliisError):
    """The upstream server returned an unexpected HTTP response."""


class EliisRateLimitError(EliisError):
    """The upstream server asked the client to slow down."""

    def __init__(self, retry_after: float | None = None) -> None:
        """Initialize the error."""
        super().__init__("Eliis rate limited the request")
        self.retry_after = retry_after


class EliisSchemaError(EliisError):
    """The response shape could not be parsed safely."""


class EliisUnsupportedError(EliisError):
    """The requested record or feature has no established representation."""
