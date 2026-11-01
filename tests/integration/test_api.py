import pytest
from fastapi.testclient import TestClient

from testledger.api import create_app
from testledger.config import Settings, hash_api_key

KEY = "test-key-123"
H = {"X-API-Key": KEY}


def xml(**outcomes):
    body = {"pass": "", "fail": '<failure message="boom"/>'}
    cases = "".join(
        f'<testcase classname="s" name="{n}" time="0.1">{body[o]}</testcase>'
        for n, o in outcomes.items()
    )
    return f"<testsuite>{cases}</testsuite>".encode()


@pytest.fixture
def make_client(engine):
    def build(**overrides):
        settings = Settings(
            api_key_hashes=hash_api_key(KEY),
            flaky_min_runs=4,
            release_after_passes=2,
            max_upload_bytes=20_000,
        ).model_copy(update=overrides)
        return TestClient(create_app(settings, engine), raise_server_exceptions=False)

    return build


@pytest.fixture
def client(make_client):
    return make_client()


def upload(client, n, payload, commit=None, project="shop", headers=H):
    params = {"external_id": f"ci-{n}", "commit_sha": commit or f"{n:07x}", "branch": "main"}
    return client.post(
        f"/v1/projects/{project}/runs",
        params=params,
        content=payload,
        headers={**headers, "Content-Type": "application/xml"},
    )


def test_health_needs_no_key(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_upload_then_idempotent_reupload(client):
    r = upload(client, 1, xml(a="pass", b="fail"))
    assert r.status_code == 201
    assert r.json() | {"run_id": 0} == {
        "run_id": 0,
        "created": True,
        "total": 2,
        "failures": 1,
        "quarantined": [],
        "released": [],
    }
    again = upload(client, 1, xml(a="pass", b="fail"))
    assert again.status_code == 200 and again.json()["created"] is False


def test_conflicting_reupload_returns_409(client):
    upload(client, 1, xml(a="pass"))
    r = upload(client, 1, xml(a="fail"))
    assert r.status_code == 409 and r.json()["error"] == "RunConflictError"


@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), ({"X-API-Key": "wrong"}, 401), ({"X-API-Key": ""}, 401)],
)
def test_auth_rejects_missing_or_bad_key(client, headers, status):
    r = upload(client, 1, xml(a="pass"), headers=headers)
    assert r.status_code == status and r.json()["error"] == "AuthError"
    assert client.get("/v1/projects/shop/runs", headers=headers).status_code == status


def test_no_configured_keys_fails_closed(make_client):
    client = make_client(api_key_hashes="")
    r = upload(client, 1, xml(a="pass"))
    assert r.status_code == 503


@pytest.mark.parametrize(
    ("project", "params", "fragment"),
    [
        ("bad name!", {}, "project"),
        ("shop", {"commit_sha": "not-hex"}, "commit_sha"),
        ("shop", {"commit_sha": "abc"}, "commit_sha"),
        ("shop", {"external_id": "has space"}, "external_id"),
        ("shop", {"branch": ""}, "branch"),
    ],
)
def test_input_validation_returns_422(client, project, params, fragment):
    base = {"external_id": "ci-1", "commit_sha": "abcdef1", "branch": "main"}
    r = client.post(
        f"/v1/projects/{project}/runs", params=base | params, content=xml(a="pass"), headers=H
    )
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_malformed_and_hostile_xml_return_422(client):
    assert upload(client, 1, b"<testsuite>").status_code == 422
    bomb = b'<!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite><testcase name="&a;"/></testsuite>'
    r = upload(client, 2, bomb)
    assert r.status_code == 422 and "unsafe XML" in r.json()["detail"]


def test_oversized_upload_returns_413(client):
    big = xml(**{f"t{i}": "pass" for i in range(400)})
    assert len(big) > 20_000
    assert upload(client, 1, big).status_code == 413


def test_request_id_is_echoed_or_generated(client):
    r = client.get("/v1/health", headers={"X-Request-ID": "abc"})
    assert r.headers["X-Request-ID"] == "abc"
    assert len(client.get("/v1/health").headers["X-Request-ID"]) == 32


def test_full_flaky_lifecycle_over_http(client):
    for n, o in enumerate(["pass", "fail", "pass", "fail"]):
        r = upload(client, n, xml(f=o, ok="pass"))
    assert r.json()["quarantined"] == ["s::f"]

    flaky = client.get("/v1/projects/shop/flaky", params={"only_flaky": True}, headers=H).json()
    assert [(v["test_id"], v["quarantined"]) for v in flaky] == [("s::f", True)]

    hist = client.get(
        "/v1/projects/shop/tests/history", params={"test_id": "s::f", "limit": 2}, headers=H
    ).json()
    assert [h["outcome"] for h in hist] == ["failed", "passed"]

    active = client.get("/v1/projects/shop/quarantine", headers=H).json()
    assert [q["test_id"] for q in active] == ["s::f"]

    for n in (4, 5):
        r = upload(client, n, xml(f="pass", ok="pass"))
    assert r.json()["released"] == ["s::f"]
    assert client.get("/v1/projects/shop/quarantine", headers=H).json() == []


def test_manual_release_and_404s(client):
    for n, o in enumerate(["pass", "fail", "pass", "fail"]):
        upload(client, n, xml(f=o))
    params = {"test_id": "s::f"}
    assert (
        client.delete("/v1/projects/shop/quarantine", params=params, headers=H).status_code == 204
    )
    r = client.delete("/v1/projects/shop/quarantine", params=params, headers=H)
    assert r.status_code == 404 and r.json()["error"] == "NotFoundError"
    assert client.get("/v1/projects/ghost/flaky", headers=H).status_code == 404


def test_run_pagination_and_detail(client):
    ids = [upload(client, n, xml(a="pass", b="fail")).json()["run_id"] for n in range(3)]
    page = client.get("/v1/projects/shop/runs", params={"limit": 2}, headers=H).json()
    assert [r["id"] for r in page["items"]] == ids[::-1][:2]
    nxt = client.get(
        "/v1/projects/shop/runs",
        params={"limit": 2, "before_id": page["next_before_id"]},
        headers=H,
    ).json()
    assert [r["id"] for r in nxt["items"]] == [ids[0]] and nxt["next_before_id"] is None
    detail = client.get(f"/v1/projects/shop/runs/{ids[0]}", headers=H).json()
    assert [f["test_id"] for f in detail["failures"]] == ["s::b"]
    assert client.get("/v1/projects/shop/runs/0", headers=H).status_code == 422


def test_unexpected_error_is_500_without_leaking_details(client, monkeypatch):
    from testledger.service import LedgerService

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(LedgerService, "list_runs", boom)
    upload(client, 1, xml(a="pass"))
    r = client.get("/v1/projects/shop/runs", headers=H)
    assert r.status_code == 500
    assert "secret" not in r.text and r.json()["error"] == "InternalError"


def test_openapi_schema_lists_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/v1/projects/{project}/runs",
        "/v1/projects/{project}/flaky",
        "/v1/projects/{project}/quarantine",
    } <= set(paths)
