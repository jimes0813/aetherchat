import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routers import (
    agent_runs,
    approvals,
    artifacts,
    desktop,
    export,
    knowledge,
    mcp,
    memory,
    models,
    plugins,
    sessions,
    tasks,
    tools,
    workspaces,
    ws,
)
from .services import tool_executor
from .services.agent_run_service import agent_run_service
from .services.local_auth import demo_token_response, require_api_access
from .services.local_model_runtime import local_model_runtime

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
    yield
    if redis_client is not None:
        await redis_client.aclose()
    await local_model_runtime.shutdown()
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
    expose_headers=["Content-Disposition", "X-Content-Type-Options"],
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
app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=protected)
app.include_router(approvals.router, prefix="/approvals", tags=["approvals"], dependencies=protected)
app.include_router(memory.router, prefix="/memory", tags=["memory"], dependencies=protected)
app.include_router(agent_runs.router, prefix="/agent-runs", tags=["agent-runs"], dependencies=protected)
app.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"], dependencies=protected)
app.include_router(mcp.router, prefix="/mcp-connections", tags=["mcp"], dependencies=protected)
app.include_router(mcp.callback_router, prefix="/mcp-connections", tags=["mcp-oauth-callback"])

@app.get("/health")
async def health():
    return {"status": "ok", "service": "aetherchat", "redis": redis_client is not None}

@app.post("/auth/token")
async def login_for_token():
    return demo_token_response()

# PR12: Simple rate limiting (per IP)
rate_limit_store: defaultdict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 60  # requests per minute
RATE_WINDOW = 60

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path == "/health" or request.method == "OPTIONS":
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if now - t < RATE_WINDOW]
    if len(rate_limit_store[ip]) >= RATE_LIMIT:
        retry_after = max(1, int(RATE_WINDOW - (now - rate_limit_store[ip][0])))
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": str(retry_after)},
        )
    rate_limit_store[ip].append(now)
    return await call_next(request)
