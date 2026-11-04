"""Self-contained async Eliis API adapter."""

from __future__ import annotations

from .client import EliisClient
from .errors import (
    EliisAuthError,
    EliisConnectionError,
    EliisError,
    EliisPermissionError,
    EliisRateLimitError,
    EliisResponseError,
    EliisSchemaError,
    EliisUnsupportedError,
)

__all__ = [
    "EliisAuthError",
    "EliisClient",
    "EliisConnectionError",
    "EliisError",
    "EliisPermissionError",
    "EliisRateLimitError",
    "EliisResponseError",
    "EliisSchemaError",
    "EliisUnsupportedError",
]
