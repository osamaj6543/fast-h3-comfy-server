"""Health, model discovery, and metrics endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse

from app.schemas import ModelCard

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request):
    """Liveness + backend connectivity (no auth, for load balancers)."""
    comfy = request.app.state.comfy
    reachable = await comfy.ping()
    db: object = request.app.state.db
    queued = db.count(("queued", "running"))
    body = {
        "status": "ok" if reachable else "degraded",
        "comfy_backend": "up" if reachable else "down",
        "active_jobs": queued,
    }
    code = http_status.HTTP_200_OK if reachable else http_status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(body, status_code=code)


@router.get("/v1/models", response_model=ModelCard)
async def models():
    """Static model card describing the FastH3 pipeline capabilities."""
    return ModelCard()


@router.get("/metrics")
async def metrics(request: Request):
    """Operational metrics: queue depth and GPU telemetry from the backend."""
    db = request.app.state.db
    comfy = request.app.state.comfy
    body = {
        "jobs": {
            "queued": db.count(("queued",)),
            "running": db.count(("running",)),
            "succeeded": db.count(("succeeded",)),
            "failed": db.count(("failed",)),
            "cancelled": db.count(("cancelled",)),
        },
        "gpu": None,
    }
    try:
        stats = await comfy.system_stats()
        body["gpu"] = [
            {
                "name": dev.get("name"),
                "type": dev.get("type"),
                "vram_total": dev.get("vram_total"),
                "vram_free": dev.get("vram_free"),
            }
            for dev in stats.get("devices", [])
        ]
    except Exception:  # noqa: BLE001 - metrics must never fail the endpoint
        log.warning("could not fetch comfy system_stats", exc_info=True)
    return body
