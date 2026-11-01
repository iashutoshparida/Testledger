"""Command line: upload a report from CI, run the evaluation, or start the server."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from testledger.evaluate import Scenario, aggregate_markdown, run_many


def _upload(args: argparse.Namespace) -> int:
    key = os.environ.get("TESTLEDGER_API_KEY")
    if not key:
        print(
            "error: set TESTLEDGER_API_KEY (never pass keys on the command line)", file=sys.stderr
        )
        return 2
    report = Path(args.report)
    if not report.is_file():
        print(f"error: {report} is not a file", file=sys.stderr)
        return 2
    try:
        response = httpx.post(
            f"{args.url.rstrip('/')}/v1/projects/{args.project}/runs",
            params={"external_id": args.run_id, "commit_sha": args.commit, "branch": args.branch},
            content=report.read_bytes(),
            headers={"X-API-Key": key, "Content-Type": "application/xml"},
            timeout=args.timeout,
        )
    except httpx.HTTPError as exc:
        print(f"error: could not reach {args.url}: {exc}", file=sys.stderr)
        return 3
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {"detail": response.text}
    )
    print(json.dumps(body, indent=2))
    if response.status_code >= 400:
        return 1
    # Let CI pipelines decide what to do: exit 0, but print what was quarantined.
    for test_id in body.get("quarantined", []):
        print(f"quarantined: {test_id}", file=sys.stderr)
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    base = Scenario()
    aggregate = run_many(range(1, args.seeds + 1), base)
    text = aggregate_markdown(base, aggregate)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _serve(args: argparse.Namespace) -> int:  # pragma: no cover - thin wrapper over uvicorn
    import uvicorn

    from testledger.api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testledger")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("upload", help="upload a JUnit XML report to a TestLedger server")
    up.add_argument("report")
    up.add_argument("--url", default=os.environ.get("TESTLEDGER_URL", "http://localhost:8000"))
    up.add_argument("--project", required=True)
    up.add_argument("--run-id", required=True)
    up.add_argument("--commit", required=True)
    up.add_argument("--branch", required=True)
    up.add_argument("--timeout", type=float, default=30.0)
    up.set_defaults(func=_upload)

    ev = sub.add_parser("evaluate", help="measure the detector on simulated CI histories")
    ev.add_argument("--seeds", type=int, default=20)
    ev.add_argument("--out")
    ev.set_defaults(func=_evaluate)

    sv = sub.add_parser("serve", help="run the API with uvicorn")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
