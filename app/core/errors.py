"""Domain error hierarchy.

Messages are written for patients: the assistant shows them verbatim, and the API
returns them in a consistent envelope (see `app.api.errors`).
"""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInputError(AppError):
    code = "invalid_input"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class SlotUnavailableError(AppError):
    status_code = 409
    code = "slot_unavailable"


class NotConfiguredError(AppError):
    code = "not_configured"


class InvalidTransitionError(AppError):
    status_code = 409
    code = "invalid_transition"
