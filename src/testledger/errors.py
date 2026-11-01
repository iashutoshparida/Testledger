"""Domain errors. The API layer maps each one to a single HTTP status."""

from __future__ import annotations


class TestLedgerError(Exception):
    """Base class for expected, user-facing failures."""

    __test__ = False  # stop pytest from collecting this as a test class


class ReportParseError(TestLedgerError):
    """The uploaded JUnit report is malformed, unsafe or empty."""


class ReportTooLargeError(TestLedgerError):
    """The uploaded report exceeds the configured size limit."""


class RunConflictError(TestLedgerError):
    """A run with the same external id exists with different content."""


class NotFoundError(TestLedgerError):
    """The requested project, run or test does not exist."""
