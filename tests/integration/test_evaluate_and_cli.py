import httpx
import pytest

from testledger import cli
from testledger.evaluate import Scenario, aggregate_markdown, run, run_many

SMALL = Scenario(seed=3, commits=60, stable_tests=20, regressions=4, flaky_probs=(0.2, 0.3, 0.4))


def test_evaluation_finds_flaky_tests_and_ignores_regressions():
    m = run(SMALL)
    assert m.true_flaky == 3
    assert m.false_positives == []
    assert m.regressions_flagged == 0
    assert m.recall >= 2 / 3
    assert m.naive_false_positives == 4  # the naive rule flags every regression


def test_evaluation_is_deterministic_per_seed():
    assert run(SMALL).detections == run(SMALL).detections


def test_aggregate_report_renders():
    agg = run_many(range(1, 3), SMALL)
    text = aggregate_markdown(SMALL, agg)
    assert "Pooled over 2 seeds" in text and "| Precision |" in text
    assert agg.precision == 1.0


def test_cli_evaluate_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Scenario", lambda: SMALL)
    out = tmp_path / "eval.md"
    assert cli.main(["evaluate", "--seeds", "1", "--out", str(out)]) == 0
    assert "Precision" in out.read_text()


@pytest.fixture
def report(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text('<testsuite><testcase name="a"/></testsuite>')
    return path


def _args(report):
    return [
        "upload",
        str(report),
        "--url",
        "http://ledger",
        "--project",
        "shop",
        "--run-id",
        "ci-1",
        "--commit",
        "abcdef1",
        "--branch",
        "main",
    ]


def test_cli_upload_requires_key_from_environment(report, monkeypatch, capsys):
    monkeypatch.delenv("TESTLEDGER_API_KEY", raising=False)
    assert cli.main(_args(report)) == 2
    assert "TESTLEDGER_API_KEY" in capsys.readouterr().err


def test_cli_upload_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTLEDGER_API_KEY", "k")
    assert cli.main(_args(tmp_path / "nope.xml")) == 2


def test_cli_upload_success_and_http_error(report, monkeypatch, capsys):
    monkeypatch.setenv("TESTLEDGER_API_KEY", "k")
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(201, json={"run_id": 1, "quarantined": ["s::t"]})

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    assert cli.main(_args(report)) == 0
    assert seen["url"] == "http://ledger/v1/projects/shop/runs"
    assert seen["headers"]["X-API-Key"] == "k"
    assert "quarantined: s::t" in capsys.readouterr().err

    monkeypatch.setattr(
        cli.httpx, "post", lambda url, **kw: httpx.Response(422, json={"detail": "bad"})
    )
    assert cli.main(_args(report)) == 1


def test_cli_upload_network_failure(report, monkeypatch):
    monkeypatch.setenv("TESTLEDGER_API_KEY", "k")

    def down(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(cli.httpx, "post", down)
    assert cli.main(_args(report)) == 3
