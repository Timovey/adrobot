"""Application (non-upstream) errors."""

from __future__ import annotations


class AppError(Exception):
    """Local error (validation, not found, conflict, persistence) mapped to an HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
