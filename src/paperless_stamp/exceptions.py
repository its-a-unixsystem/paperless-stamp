"""Exception hierarchy for paperless-stamp."""


class PaperlessStampError(Exception):
    """Base exception for all paperless-stamp errors."""


class PaperlessAPIError(PaperlessStampError):
    """Raised when the Paperless-ngx API returns an error response."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        if detail:
            msg = f"API error {status_code}: {detail}"
        else:
            msg = f"API error {status_code}"
        super().__init__(msg)


class PaperlessConnectionError(PaperlessStampError):
    """Raised when unable to connect to the Paperless-ngx instance."""


class PaperlessAuthError(PaperlessStampError):
    """Raised when authentication with the Paperless-ngx API fails."""


class StampError(PaperlessStampError):
    """Base exception for stamp-related errors."""


class StampGenerationError(StampError):
    """Raised when stamp overlay generation fails."""


class StampMergeError(StampError):
    """Raised when merging a stamp overlay into a PDF fails."""
