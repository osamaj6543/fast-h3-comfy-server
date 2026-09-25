"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import Settings, get_settings
from app.db import JobStore
from app.engine.comfy_client import ComfyClient
from app.events import EventBus
from app.routers import system, videos
from app.storage import Storage
from app.worker import Worker

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Services
        app.state.settings = settings
        app.state.db = JobStore(settings.db_path)
        app.state.storage = Storage(settings.storage_dir, settings.uploads_dir)
        app.state.comfy = ComfyClient(settings.comfy_url)
        app.state.bus = EventBus()
        app.state.worker = Worker(
            settings, app.state.db, app.state.comfy, app.state.storage, app.state.bus
        )
        if not settings.auth_enabled:
            log.warning("API key auth is DISABLED (FASTH3_API_KEYS empty) - dev mode")
        await app.state.worker.start()
        log.info("FastH3 Comfy Server v%s ready (comfy=%s)", __version__, settings.comfy_url)
        yield
        await app.state.worker.stop()
        await app.state.comfy.close()
        app.state.db.close()

    app = FastAPI(
        title="FastH3 Comfy Server",
        description=(
            "Professional API for MiniMax H3 video generation via the "
            "FastVideo FastH3 8-step distilled checkpoint, with synchronized "
            "native audio. Backed by a headless ComfyUI engine."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten for your frontend in production
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type"],
    )
    app.include_router(system.router)
    app.include_router(videos.router)
    return app


app = create_app()
