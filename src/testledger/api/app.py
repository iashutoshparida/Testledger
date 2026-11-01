"""HTTP layer: validation, auth, error mapping. All logic lives in LedgerService."""

from __future__ import annotations

import hmac
import logging
import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from testledger import __version__
from testledger.api.schemas import (
    ErrorOut,
    FailureOut,
    HistoryOut,
    IngestOut,
    QuarantineOut,
    RunDetailOut,
    RunOut,
    RunPage,
    VerdictOut,
)
from testledger.config import Settings, get_settings, hash_api_key
from testledger.db import make_engine, make_session_factory
from testledger.errors import (
    NotFoundError,
    ReportParseError,
    ReportTooLargeError,
    RunConflictError,
    TestLedgerError,
)
from testledger.logging_setup import configure_logging
from testledger.service import LedgerService

log = logging.getLogger("testledger.api")

_STATUS: dict[type[TestLedgerError], int] = {
    ReportParseError: 422,
    ReportTooLargeError: 413,
    RunConflictError: 409,
    NotFoundError: 404,
}

ProjectName = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]
TestId = Annotated[str, Query(min_length=1, max_length=500)]


class AuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status, self.detail = status, detail


class RequestIdMiddleware:
    """Pure ASGI middleware: tags every request with an id and echoes it in X-Request-ID.
    (Starlette's BaseHTTPMiddleware leaks streams when a handler raises.)"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"")[:64].decode("latin-1")
        rid = incoming or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = rid
            await send(message)

        await self.app(scope, receive, send_with_id)


def get_service(request: Request) -> LedgerService:
    service: LedgerService = request.app.state.service
    return service


def require_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    key_hashes: frozenset[str] = request.app.state.key_hashes
    if not key_hashes:
        raise AuthError(503, "server has no API keys configured")
    if not x_api_key:
        raise AuthError(401, "missing X-API-Key header")
    presented = hash_api_key(x_api_key)
    # Compare against every hash so timing does not reveal which key nearly matched.
    matches = [hmac.compare_digest(presented, h) for h in key_hashes]
    if not any(matches):
        raise AuthError(401, "invalid API key")


Svc = Annotated[LedgerService, Depends(get_service)]
Auth = Depends(require_key)


def _error(status: int, error: str, detail: str, request: Request) -> JSONResponse:
    rid = getattr(request.state, "request_id", "")
    body = ErrorOut(error=error, detail=detail, request_id=rid)
    return JSONResponse(status_code=status, content=body.model_dump())


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = engine or make_engine(settings.database_url)
    service = LedgerService(make_session_factory(engine), settings)
    key_hashes = settings.accepted_key_hashes()
    if not key_hashes:
        log.warning("no API keys configured: every protected endpoint will return 503")

    app = FastAPI(
        title="TestLedger",
        version=__version__,
        description="CI test-result ledger with flaky-test detection and quarantine.",
    )
    app.state.service = service
    app.state.engine = engine
    app.state.key_hashes = key_hashes
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(TestLedgerError)
    async def domain_error(request: Request, exc: TestLedgerError) -> JSONResponse:
        status = _STATUS.get(type(exc), 400)
        return _error(status, type(exc).__name__, str(exc), request)

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return _error(exc.status, "AuthError", exc.detail, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return _error(422, "ValidationError", detail, request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled error", extra={"request_id": getattr(request.state, "request_id", "")}
        )
        return _error(500, "InternalError", "unexpected server error", request)

    @app.get("/v1/health", tags=["ops"])
    def health() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    @app.post(
        "/v1/projects/{project}/runs",
        response_model=IngestOut,
        status_code=201,
        dependencies=[Auth],
        tags=["runs"],
        responses={
            200: {"description": "identical run already stored (idempotent re-upload)"},
            409: {"model": ErrorOut},
            413: {"model": ErrorOut},
            422: {"model": ErrorOut},
        },
    )
    async def upload_run(
        request: Request,
        response: Response,
        project: ProjectName,
        ledger: Svc,
        external_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9._:/-]{1,200}$")],
        commit_sha: Annotated[str, Query(pattern=r"^[0-9a-fA-F]{7,64}$")],
        branch: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> IngestOut:
        """Upload one JUnit XML report as the raw request body."""
        limit = settings.max_upload_bytes
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise ReportTooLargeError(f"declared size {declared} bytes exceeds {limit}")
        body = bytearray()
        async for chunk in request.stream():  # stop reading as soon as the limit is crossed
            body.extend(chunk)
            if len(body) > limit:
                raise ReportTooLargeError(f"report exceeds {limit} bytes")
        # Parsing and DB writes block; keep them off the event loop.
        result = await run_in_threadpool(
            ledger.ingest, project, external_id, commit_sha, branch, bytes(body)
        )
        if not result.created:
            response.status_code = 200
        return IngestOut(**asdict(result))

    @app.get(
        "/v1/projects/{project}/runs", response_model=RunPage, dependencies=[Auth], tags=["runs"]
    )
    def list_runs(
        project: ProjectName,
        service: Svc,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        before_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> RunPage:
        runs = service.list_runs(project, limit, before_id)
        nxt = runs[-1].id if len(runs) == limit else None
        return RunPage(items=[RunOut(**asdict(r)) for r in runs], next_before_id=nxt)

    @app.get(
        "/v1/projects/{project}/runs/{run_id}",
        response_model=RunDetailOut,
        dependencies=[Auth],
        tags=["runs"],
    )
    def run_detail(
        project: ProjectName, run_id: Annotated[int, Path(ge=1)], service: Svc
    ) -> RunDetailOut:
        summary, failures = service.run_failures(project, run_id)
        return RunDetailOut(
            run=RunOut(**asdict(summary)), failures=[FailureOut(**asdict(f)) for f in failures]
        )

    @app.get(
        "/v1/projects/{project}/flaky",
        response_model=list[VerdictOut],
        dependencies=[Auth],
        tags=["flakiness"],
    )
    def flaky(project: ProjectName, service: Svc, only_flaky: bool = False) -> list[VerdictOut]:
        out = []
        for tv in service.flaky_report(project):
            v = tv.verdict
            if only_flaky and not v.is_flaky:
                continue
            out.append(
                VerdictOut(
                    test_id=tv.test_id,
                    runs=v.runs,
                    failures=v.failures,
                    flips=v.flips,
                    flip_rate=v.flip_rate,
                    mixed_commits=v.mixed_commits,
                    score=v.score,
                    is_flaky=v.is_flaky,
                    quarantined=tv.quarantined,
                    reason=v.reason,
                )
            )
        return out

    @app.get(
        "/v1/projects/{project}/tests/history",
        response_model=list[HistoryOut],
        dependencies=[Auth],
        tags=["flakiness"],
    )
    def history(
        project: ProjectName,
        test_id: TestId,
        service: Svc,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[HistoryOut]:
        return [HistoryOut(**asdict(h)) for h in service.test_history(project, test_id, limit)]

    @app.get(
        "/v1/projects/{project}/quarantine",
        response_model=list[QuarantineOut],
        dependencies=[Auth],
        tags=["quarantine"],
    )
    def quarantine(project: ProjectName, service: Svc, active: bool = True) -> list[QuarantineOut]:
        return [QuarantineOut(**asdict(q)) for q in service.quarantine_list(project, active)]

    @app.delete(
        "/v1/projects/{project}/quarantine",
        status_code=204,
        dependencies=[Auth],
        tags=["quarantine"],
    )
    def release(project: ProjectName, test_id: TestId, service: Svc) -> Response:
        service.release(project, test_id)
        return Response(status_code=204)

    return app
