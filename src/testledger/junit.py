"""Parse JUnit XML reports (pytest, Maven Surefire, Gradle, Jest-junit) into plain records.

Reports come from CI machines and are treated as untrusted input:
* the byte size is checked before any parsing,
* DTDs and entity declarations are refused (no XXE, no billion-laughs),
* every field is validated and long failure messages are truncated.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from testledger.errors import ReportParseError, ReportTooLargeError

MAX_MESSAGE_CHARS = 2000


class Outcome(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"

    @property
    def is_failure(self) -> bool:
        return self in (Outcome.FAILED, Outcome.ERROR)


# Higher rank wins when the same test appears twice in one report.
_RANK = {Outcome.SKIPPED: 0, Outcome.PASSED: 1, Outcome.FAILED: 2, Outcome.ERROR: 3}


@dataclass(frozen=True, slots=True)
class TestResult:
    __test__ = False

    test_id: str
    outcome: Outcome
    duration_ms: int
    message: str | None = None


def parse_report(data: bytes, max_bytes: int) -> list[TestResult]:
    """Return one result per distinct test id, ordered by first appearance."""
    if len(data) > max_bytes:
        raise ReportTooLargeError(f"report is {len(data)} bytes; limit is {max_bytes}")
    if not data.strip():
        raise ReportParseError("report is empty")
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ReportParseError(f"unsafe XML refused: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise ReportParseError(f"malformed XML: {exc}") from exc

    if root.tag not in ("testsuites", "testsuite"):
        raise ReportParseError(f"expected <testsuites> or <testsuite> root, got <{root.tag}>")

    merged: dict[str, TestResult] = {}
    for case in root.iter("testcase"):
        result = _parse_case(case)
        previous = merged.get(result.test_id)
        if previous is None or _RANK[result.outcome] > _RANK[previous.outcome]:
            merged[result.test_id] = result
    if not merged:
        raise ReportParseError("report contains no <testcase> elements")
    return list(merged.values())


def _parse_case(case: Element) -> TestResult:
    name = (case.get("name") or "").strip()
    if not name:
        raise ReportParseError("<testcase> without a name attribute")
    classname = (case.get("classname") or "").strip()
    test_id = f"{classname}::{name}" if classname else name
    if len(test_id) > 500:
        raise ReportParseError(f"test id longer than 500 characters: {test_id[:60]}...")

    outcome = Outcome.PASSED
    message: str | None = None
    for tag, candidate in (
        ("error", Outcome.ERROR),
        ("failure", Outcome.FAILED),
        ("skipped", Outcome.SKIPPED),
    ):
        node = case.find(tag)
        if node is not None:
            outcome = candidate
            text = node.get("message") or (node.text or "")
            message = text.strip()[:MAX_MESSAGE_CHARS] or None
            break

    return TestResult(test_id, outcome, _duration_ms(case.get("time"), test_id), message)


def _duration_ms(raw: str | None, test_id: str) -> int:
    if raw is None or not raw.strip():
        return 0
    try:
        seconds = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ReportParseError(f"invalid time {raw!r} for {test_id}") from exc
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
        raise ReportParseError(f"invalid time {raw!r} for {test_id}")
    return round(seconds * 1000)
