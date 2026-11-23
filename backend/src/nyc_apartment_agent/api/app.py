"""Thin FastAPI layer over the resolver.

Endpoints
- GET  /health   -> liveness only; never calls external sources.
- POST /resolve  -> {"address": "..."} -> ResolutionResult.
    200 for verified / ambiguous / no_reliable_match,
    503 for source_unavailable (same typed body, plus Retry-After),
    422 for invalid requests, 500 for unexpected errors (no internal details leaked).

Every response carries an X-Request-ID header so a result can be traced in the logs.
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .. import __version__
from ..config import get_settings
from ..http_client import SourceHttpClient
from ..investigation.agent import investigate
from ..investigation.baseline import demo_examples
from ..investigation.llm import llm_status
from ..investigation.render import render_markdown
from ..investigation.schema import ResearchReport
from ..resolver import Outcome, ResolutionResult, resolve_address
from ..resolver.normalize import MAX_INPUT_CHARS
from ..sources.clients import geosearch_client, socrata_client

logger = logging.getLogger(__name__)

AddressText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_INPUT_CHARS)
]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: AddressText = Field(
        description="An NYC street address, e.g. '1134 West Farms Rd, Bronx, NY 10459'."
    )


class SelectedOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbl: Annotated[str, StringConstraints(pattern=r"^[1-5]\d{9}$")]
    bin: Annotated[str, StringConstraints(pattern=r"^[1-5]\d{6}$")] | None = None


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: AddressText
    selected_option: SelectedOption | None = None
    use_llm: bool = True


class _ReportCache:
    """Recent reports in memory only (non-persistent), for the export links."""

    def __init__(self, size: int = 32) -> None:
        self._items: OrderedDict[str, ResearchReport] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def put(self, r: ResearchReport) -> None:
        with self._lock:
            self._items[r.report_id] = r
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def get(self, rid: str) -> ResearchReport | None:
        with self._lock:
            return self._items.get(rid)


class _RateLimiter:
    """Per-client sliding window for the expensive endpoint (public demo protection)."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: str
    fields: list[dict] | None = None


ClientPair = tuple[SourceHttpClient, SourceHttpClient]


def default_clients() -> Iterator[ClientPair]:
    gs, sc = geosearch_client(), socrata_client()
    try:
        yield gs, sc
    finally:
        gs.close()
        sc.close()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def create_app(
    clients_dependency: Callable[..., Iterator[ClientPair]] = default_clients,
) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NYC Apartment Research Agent API",
        version=__version__,
        description="Address resolution and evidence-grounded building reports from official "
        "NYC records.",
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
        expose_headers=["X-Request-ID"],
    )
    cache = _ReportCache()
    limiter = _RateLimiter(settings.investigate_rate_per_minute)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # Report where and why, but never echo the submitted values back.
        fields = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "invalid")}
            for e in exc.errors()
        ]
        body = ErrorResponse(
            error="invalid_request",
            message='The request body must be JSON like {"address": "350 5th Ave, Manhattan"}'
            f" with 1 to {MAX_INPUT_CHARS} characters.",
            request_id=_request_id(request),
            fields=fields,
        )
        return JSONResponse(
            status_code=422,
            content=body.model_dump(),
            headers={"X-Request-ID": _request_id(request)},
        )

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, exc: Exception):
        logger.exception("unhandled error, request_id=%s", _request_id(request))
        body = ErrorResponse(
            error="internal_error",
            message="Something went wrong on our side. No result was produced.",
            request_id=_request_id(request),
        )
        return JSONResponse(
            status_code=500,
            content=body.model_dump(),
            headers={"X-Request-ID": _request_id(request)},
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/config")
    def config() -> dict:
        s = get_settings()
        ok, why = llm_status(s)
        return {
            "data_mode": s.data_mode,
            "llm_available": ok,
            "llm_detail": why,
            "llm_provider": s.llm_provider if ok else None,
            "examples": demo_examples(),
            "version": __version__,
        }

    @app.post(
        "/investigate",
        response_model=ResearchReport,
        response_model_exclude_none=True,
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request"},
            429: {"model": ErrorResponse, "description": "Too many requests"},
        },
    )
    def investigate_endpoint(body: InvestigateRequest, request: Request):
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse(
                status_code=429,
                content=ErrorResponse(
                    error="rate_limited",
                    message="Too many investigations from this address. Try again in a minute.",
                    request_id=_request_id(request),
                ).model_dump(),
                headers={"Retry-After": "60"},
            )
        selected = body.selected_option.model_dump() if body.selected_option else None
        report = investigate(body.address, selected=selected, use_llm=body.use_llm)
        cache.put(report)
        return report

    @app.get("/reports/{report_id}.md", response_class=PlainTextResponse)
    def report_markdown(report_id: str):
        r = cache.get(report_id)
        if r is None:
            raise HTTPException(status_code=404, detail="report expired or unknown")
        return PlainTextResponse(
            render_markdown(r),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.md"'},
        )

    @app.get("/reports/{report_id}.json")
    def report_json(report_id: str):
        r = cache.get(report_id)
        if r is None:
            raise HTTPException(status_code=404, detail="report expired or unknown")
        return Response(
            r.model_dump_json(exclude_none=True, indent=1),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.json"'},
        )

    @app.post(
        "/resolve",
        response_model=ResolutionResult,
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request"},
            500: {"model": ErrorResponse, "description": "Unexpected server error"},
            503: {
                "model": ResolutionResult,
                "description": "outcome = source_unavailable: an official source failed",
            },
        },
    )
    def resolve(
        body: ResolveRequest,
        request: Request,
        clients: Annotated[ClientPair, Depends(clients_dependency)],
    ):
        gs, sc = clients
        result = resolve_address(body.address, gs, sc)
        result.provenance["request_id"] = _request_id(request)
        if result.outcome is Outcome.SOURCE_UNAVAILABLE:
            return JSONResponse(
                status_code=503,
                content=result.model_dump(mode="json"),
                headers={"Retry-After": "30"},
            )
        return result

    return app


app = create_app()
