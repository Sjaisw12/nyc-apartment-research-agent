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
import uuid
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .. import __version__
from ..http_client import SourceHttpClient
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
    app = FastAPI(
        title="NYC Apartment Research Agent API",
        version=__version__,
        description="Phase 1: address resolution against official NYC records.",
    )

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
