"""Failures from the fetch seam and the append-only store."""


class FetchError(Exception):
    """A source file could not be downloaded or read."""


class IngestError(Exception):
    """A row or table broke an ingestion or point-in-time rule."""
