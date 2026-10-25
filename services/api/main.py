"""
FastAPI application entry point.

Lifespan: creates the DB engine pool on startup, disposes it on shutdown.
SQLAlchemy's async engine is NOT a context manager itself — we manage its lifecycle
here so the pool isn't recreated per-request.

CORS is wide-open in dev (allow_origins=["*"]).
The edge compose file will tighten this to the operator terminal's actual IP.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import engine
from .routes import health, incidents, stream, tracks


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: engine is initialized lazily on first query — nothing to do here
    yield
    # Shutdown: close all pooled connections cleanly
    await engine.dispose()


app = FastAPI(
    title="Edge Base-Defense Copilot API",
    description="REST + SSE API for the operator terminal. All endpoints require Bearer auth except /health.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened in docker-compose.edge.yml
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health.router)
app.include_router(incidents.router)
app.include_router(tracks.router)
app.include_router(stream.router)
