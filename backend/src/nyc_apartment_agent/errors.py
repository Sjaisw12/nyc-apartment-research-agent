"""Errors raised when an external data source cannot give us a usable answer.

Each error names the source and the URL so failures can be logged and shown to the
user as "this source was unavailable", never silently treated as "no records found".
"""


class SourceError(Exception):
    """Base class for every data-source failure."""

    def __init__(self, source: str, url: str, message: str, attempts: int = 1) -> None:
        self.source = source
        self.url = url
        self.attempts = attempts
        super().__init__(f"[{source}] {message} (url={url}, attempts={attempts})")


class SourceTimeoutError(SourceError):
    """The source did not answer within the timeout, after all retries."""


class SourceConnectionError(SourceError):
    """We could not connect to the source (DNS, TLS, refused), after all retries."""


class SourceRateLimitedError(SourceError):
    """The source kept returning HTTP 429, or asked us to wait longer than we allow."""


class SourceServerError(SourceError):
    """The source kept returning 5xx responses."""

    def __init__(self, source: str, url: str, status_code: int, attempts: int) -> None:
        self.status_code = status_code
        super().__init__(source, url, f"server error HTTP {status_code}", attempts)


class SourceClientError(SourceError):
    """The source rejected our request (4xx other than 429). Not retried: it is our bug."""

    def __init__(self, source: str, url: str, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(source, url, f"client error HTTP {status_code}: {detail}")


class SourceResponseError(SourceError):
    """The source answered 200 but the body was not the JSON shape we expect."""


class InvalidIdentifierError(ValueError):
    """A BBL, BIN or other key failed validation before we sent it to a source."""
