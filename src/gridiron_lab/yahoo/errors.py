"""Failures from the Yahoo Fantasy client. Tokens never go in these messages."""


class YahooError(Exception):
    """The Yahoo request failed after the retries this client allows."""


class YahooAuthError(YahooError):
    """Authorization or token exchange failed."""


class YahooConfigError(YahooError):
    """A required Yahoo setting or environment variable is missing."""


class YahooRateLimitError(YahooError):
    """Yahoo kept returning a rate-limit response."""
