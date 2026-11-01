import pytest

from testledger.errors import ReportParseError, ReportTooLargeError
from testledger.junit import MAX_MESSAGE_CHARS, Outcome, parse_report

LIMIT = 1_000_000

PYTEST_REPORT = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="tests.test_cart" name="test_add" time="0.012"/>
  <testcase classname="tests.test_cart" name="test_remove" time="0.5">
    <failure message="assert 2 == 3">trace</failure>
  </testcase>
  <testcase classname="tests.test_db" name="test_connect" time="1.25">
    <error message="ConnectionRefused"/>
  </testcase>
  <testcase classname="tests.test_db" name="test_slow" time="0">
    <skipped message="slow"/>
  </testcase>
</testsuite></testsuites>"""


def test_parses_all_outcomes_and_durations():
    results = {r.test_id: r for r in parse_report(PYTEST_REPORT, LIMIT)}
    assert results["tests.test_cart::test_add"].outcome is Outcome.PASSED
    assert results["tests.test_cart::test_add"].duration_ms == 12
    assert results["tests.test_cart::test_remove"].outcome is Outcome.FAILED
    assert results["tests.test_cart::test_remove"].message == "assert 2 == 3"
    assert results["tests.test_db::test_connect"].outcome is Outcome.ERROR
    assert results["tests.test_db::test_slow"].outcome is Outcome.SKIPPED
    assert results["tests.test_db::test_connect"].duration_ms == 1250


def test_single_testsuite_root_and_missing_classname():
    data = b'<testsuite><testcase name="loose" time="0.1"/></testsuite>'
    [result] = parse_report(data, LIMIT)
    assert result.test_id == "loose"


def test_surefire_style_nested_suites():
    data = (
        b'<testsuites><testsuite name="a"><testsuite name="b">'
        b'<testcase classname="com.acme.CartTest" name="adds" time="1,204.5"/>'
        b"</testsuite></testsuite></testsuites>"
    )
    [result] = parse_report(data, LIMIT)
    assert result.test_id == "com.acme.CartTest::adds"
    assert result.duration_ms == 1_204_500


def test_duplicate_test_keeps_worst_outcome():
    data = (
        b'<testsuite><testcase classname="c" name="t"/>'
        b'<testcase classname="c" name="t"><failure message="boom"/></testcase>'
        b'<testcase classname="c" name="t"><skipped/></testcase></testsuite>'
    )
    [result] = parse_report(data, LIMIT)
    assert result.outcome is Outcome.FAILED


def test_failure_message_falls_back_to_text_and_is_truncated():
    long = "x" * (MAX_MESSAGE_CHARS + 50)
    data = f'<testsuite><testcase name="t"><failure>{long}</failure></testcase></testsuite>'
    [result] = parse_report(data.encode(), LIMIT)
    assert result.message is not None and len(result.message) == MAX_MESSAGE_CHARS


def test_size_limit_checked_before_parsing():
    with pytest.raises(ReportTooLargeError):
        parse_report(b"<testsuite>" + b" " * 100 + b"</testsuite>", max_bytes=50)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   \n",
        b"<testsuite><testcase name='a'>",  # truncated
        b"<html><body/></html>",  # wrong root
        b"<testsuite></testsuite>",  # no test cases
        b"<testsuite><testcase classname='x'/></testsuite>",  # no name
        b"<testsuite><testcase name='a' time='fast'/></testsuite>",
        b"<testsuite><testcase name='a' time='-1'/></testsuite>",
        b"<testsuite><testcase name='a' time='nan'/></testsuite>",
    ],
)
def test_rejects_malformed_reports(data):
    with pytest.raises(ReportParseError):
        parse_report(data, LIMIT)


def test_rejects_entity_expansion_attack():
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<testsuite><testcase name="&lol2;"/></testsuite>"""
    with pytest.raises(ReportParseError, match="unsafe XML"):
        parse_report(bomb, LIMIT)


def test_rejects_external_entity():
    xxe = b"""<?xml version="1.0"?>
<!DOCTYPE t [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<testsuite><testcase name="&xxe;"/></testsuite>"""
    with pytest.raises(ReportParseError, match="unsafe XML"):
        parse_report(xxe, LIMIT)


def test_rejects_overlong_test_id():
    data = f'<testsuite><testcase name="{"n" * 600}"/></testsuite>'.encode()
    with pytest.raises(ReportParseError, match="longer than 500"):
        parse_report(data, LIMIT)
