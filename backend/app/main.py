import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models.schemas import HealthStatus, ReadinessStatus
from .request_context import normalize_request_id, reset_request_id, set_request_id
from .routers import (
    agent_runs,
    approvals,
    artifacts,
    automations,
    desktop,
    diagnostics,
    export,
    instructions,
    knowledge,
    mcp,
    memory,
    models,
    platform,
    plugins,
    sessions,
    tasks,
    tools,
    voice,
    workspaces,
    ws,
)
from .services import tool_executor
from .services.agent_run_service import agent_run_service
from .services.automation_service import automation_service
from .services.comfyui_runtime import comfyui_runtime
from .services.local_auth import demo_token_response, require_api_access
from .services.local_model_runtime import local_model_runtime
from .services.readiness_service import readiness_service
from .services.run_dispatcher import run_dispatcher
from .services.structured_logging import configure_structured_logging
from .services.voice_runtime import voice_runtime

logger = logging.getLogger(__name__)
structured_log_path = configure_structured_logging()
_SAFE_ROUTE_PREFIX_RE = re.compile(r"(?:/[A-Za-z0-9._~-]+)*")

# 全局 Redis
redis_client: redis.Redis | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    try:
        redis_client = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
        await redis_client.ping()
    except Exception:
        redis_client = None
    tasks.redis_client = redis_client
    tool_executor.redis_client = redis_client
    agent_run_service.recover()
    await run_dispatcher.start()
    await automation_service.start()
    yield
    await automation_service.stop()
    await run_dispatcher.stop()
    if redis_client is not None:
        await redis_client.aclose()
    await comfyui_runtime.shutdown()
    await local_model_runtime.shutdown()
    await voice_runtime.shutdown()
    tasks.redis_client = None
    tool_executor.redis_client = None

app = FastAPI(title="AetherChat API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Content-Type-Options", "X-Request-ID"],
)


def _request_id_from(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or normalize_request_id())


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    relative = str(getattr(route, "path", "") or "")
    if not relative:
        return "unmatched"
    root_path = str(request.scope.get("root_path") or "").rstrip("/")
    if root_path and relative != root_path and not relative.startswith(f"{root_path}/"):
        return f"{root_path}/{relative.lstrip('/')}"
    if root_path:
        return relative

    full_path = str(request.scope.get("path") or "")
    path_regex = getattr(route, "path_regex", None)
    if full_path and path_regex is not None:
        for index, character in enumerate(full_path):
            if character != "/" or path_regex.fullmatch(full_path[index:]) is None:
                continue
            prefix = full_path[:index]
            if _SAFE_ROUTE_PREFIX_RE.fullmatch(prefix):
                return f"{prefix}{relative}"
    return relative


@app.exception_handler(HTTPException)
async def http_exception_with_request_id(request: Request, exc: HTTPException):
    request_id = _request_id_from(request)
    headers = dict(exc.headers or {})
    headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail), "request_id": request_id},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_with_request_id(
    request: Request,
    exc: RequestValidationError,
):
    request_id = _request_id_from(request)
    return JSONResponse(
        status_code=422,
        content={
            "detail": jsonable_encoder(exc.errors()),
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

protected = [Depends(require_api_access)]
app.include_router(ws.router, prefix="/ws")
app.include_router(sessions.router, prefix="/sessions", tags=["sessions"], dependencies=protected)
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"], dependencies=protected)
app.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"], dependencies=protected)
app.include_router(tools.router, prefix="/tools", tags=["tools"], dependencies=protected)
app.include_router(export.router, prefix="/export", tags=["export"], dependencies=protected)
app.include_router(models.router, prefix="/models", tags=["models"], dependencies=protected)
app.include_router(plugins.router, prefix="/plugins", tags=["plugins"], dependencies=protected)
app.include_router(desktop.router, prefix="/desktop", tags=["desktop"], dependencies=protected)
app.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"], dependencies=protected)
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=protected)
app.include_router(approvals.router, prefix="/approvals", tags=["approvals"], dependencies=protected)
app.include_router(memory.router, prefix="/memory", tags=["memory"], dependencies=protected)
app.include_router(instructions.router, prefix="/instructions", tags=["instructions"], dependencies=protected)
# Token-authenticated workflow webhooks (no session JWT; token is the secret).
app.include_router(instructions.webhook_router, prefix="/instructions", tags=["instructions-webhooks"])
app.include_router(platform.router, prefix="/platform", tags=["platform"], dependencies=protected)
app.include_router(agent_runs.router, prefix="/agent-runs", tags=["agent-runs"], dependencies=protected)
app.include_router(automations.router, prefix="/automations", tags=["automations"], dependencies=protected)
app.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"], dependencies=protected)
app.include_router(mcp.router, prefix="/mcp-connections", tags=["mcp"], dependencies=protected)
app.include_router(mcp.callback_router, prefix="/mcp-connections", tags=["mcp-oauth-callback"])
app.include_router(voice.router, prefix="/voice", tags=["voice"], dependencies=protected)


@app.get("/health", response_model=HealthStatus)
async def health():
    return {
        "status": "ok",
        "service": "aetherchat",
        "redis": redis_client is not None,
        "automation": automation_service.liveness_status(),
        "run_dispatcher": run_dispatcher.liveness_status(),
    }


@app.get("/health/ready", response_model=ReadinessStatus)
async def readiness(request: Request):
    result = readiness_service.evaluate(
        optional_statuses={
            "redis": "ready" if redis_client is not None else "unavailable",
            "automation": "ready" if automation_service.running else "unavailable",
            "comfyui": "not_probed",
            "local_models": "not_probed",
        }
    )
    result["request_id"] = _request_id_from(request)
    return JSONResponse(
        status_code=200 if result["ready"] else 503,
        content=result,
    )

@app.post("/auth/token")
async def login_for_token():
    return demo_token_response()

# PR12: Simple rate limiting (per IP)
rate_limit_store: defaultdict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 60  # requests per minute
RATE_WINDOW = 60
RATE_LIMIT_EXEMPT_PATHS = frozenset({"/health", "/health/ready"})
RATE_LIMIT_EXEMPT_PREFIXES = ("/voice/",)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if (
        request.url.path in RATE_LIMIT_EXEMPT_PATHS
        or request.url.path.startswith(RATE_LIMIT_EXEMPT_PREFIXES)
        or request.method == "OPTIONS"
    ):
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if now - t < RATE_WINDOW]
    if len(rate_limit_store[ip]) >= RATE_LIMIT:
        retry_after = max(1, int(RATE_WINDOW - (now - rate_limit_store[ip][0])))
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "request_id": _request_id_from(request),
            },
            headers={"Retry-After": str(retry_after)},
        )
    rate_limit_store[ip].append(now)
    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    request_id = normalize_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    token = set_request_id(request_id)
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled HTTP request failure",
                extra={
                    "event": "http_request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": _route_template(request),
                },
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
            )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http_request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": _route_template(request),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return response
    finally:
        reset_request_id(token)
