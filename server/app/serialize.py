"""Serialize DB job rows into the public API representation."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.engine import h3
from app.schemas import VideoJob, safe_preview
from app.security import make_signed_token


def job_to_api(request, job: dict) -> VideoJob:
    """Build the API view of a job (needs `request` for URL building)."""
    settings = request.app.state.settings
    params = job["params"]
    base = str(request.base_url).rstrip("/")

    links = {
        "self": f"{base}/v1/videos/{job['id']}",
        "events": f"{base}/v1/videos/{job['id']}/events",
    }
    if job["status"] == "succeeded":
        token, expires = make_signed_token(
            settings, job["id"], settings.result_url_ttl_seconds
        )
        links["content"] = (
            f"{base}/v1/videos/{job['id']}/content?token={token}&expires={expires}"
        )

    return VideoJob(
        id=job["id"],
        kind=job["kind"],
        status=job["status"],
        prompt_preview=safe_preview(params.get("prompt", "")),
        created_at=datetime.fromisoformat(job["created_at"]),
        updated_at=datetime.fromisoformat(job["updated_at"]),
        duration_seconds=params["duration_seconds"],
        effective_frames=h3.frames_for_duration(params["duration_seconds"]),
        effective_width=params.get("effective_width"),
        effective_height=params.get("effective_height"),
        seed=params["seed"],
        progress=min(1.0, max(0.0, job["progress"])),
        queue_position=job["queue_position"],
        error=job["error"],
        links=links,
    )


def status_payload(job: dict) -> dict[str, Any]:
    """Compact payload pushed over SSE."""
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": min(1.0, max(0.0, job["progress"])),
        "queue_position": job["queue_position"],
        "status_message": job["status_message"],
        "error": job["error"],
    }
