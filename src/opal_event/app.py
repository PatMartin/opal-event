"""FastAPI application factory: router wiring, exception handlers, and the lifespan that starts/
stops the shared :class:`~opal_event.service.EventService` (config, db, broker, executor)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from opal_event.config import ConfigStore
from opal_event.errors import OpalContextualError, ProblemError, opal_contextual_error_handler, problem_exception_handler
from opal_event.opal_context import OpalContextMiddleware
from opal_event.routers import actions, backup, events, executions, operations, rules, settings
from opal_event.service import EventService
from opal_event.validation import ValidationProblem

logger = logging.getLogger("opal_event.app")


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


async def _validation_problem_handler(request: Request, exc: ValidationProblem) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.detail)})


async def _request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


def create_app(config_path: str) -> FastAPI:
    config = ConfigStore(config_path)
    server_settings = config.settings.server
    if not _is_loopback(server_settings.host) and not config.settings.security.rest_auth.enabled:
        raise RuntimeError(
            "refusing to start: server.host is non-loopback but security.rest_auth.enabled is false "
            "(REST bearer auth is required on non-loopback interfaces)"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = EventService(config)
        app.state.config = config
        app.state.service = service
        service.start()
        watch_task = asyncio.create_task(service.watch_config_file())
        try:
            yield
        finally:
            watch_task.cancel()
            service.stop()

    app = FastAPI(
        title="Opal Event API",
        version="1.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(OpalContextMiddleware)
    app.add_exception_handler(ValidationProblem, _validation_problem_handler)
    app.add_exception_handler(ProblemError, problem_exception_handler)
    app.add_exception_handler(OpalContextualError, opal_contextual_error_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)

    app.include_router(events.router)
    app.include_router(rules.router)
    app.include_router(actions.router)
    app.include_router(executions.router)
    app.include_router(operations.router)
    app.include_router(settings.router)
    app.include_router(backup.router)

    return app
