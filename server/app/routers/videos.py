"""Core video generation endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi import status as http_status
from fastapi.params import Query
from fastapi.responses import FileResponse, StreamingResponse

from app.db import JobStore
from app.engine import h3
from app.schemas import VideoCreateRequest, VideoJob, VideoListResponse
from app.security import ensure_key, require_api_key, verify_signed_token
from app.serialize import job_to_api, status_payload

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/videos", tags=["videos"])

POLL_DONE = ("succeeded", "failed", "cancelled")


def _db(request: Request) -> JobStore:
    return request.app.state.db


@router.post("", response_model=VideoJob, status_code=http_status.HTTP_202_ACCEPTED)
async def create_video(
    request: Request,
    api_key: str = Depends(require_api_key),
    payload: Optional[str] = Form(None, alias="request"),
    first_frame: Optional[UploadFile] = File(None),
    last_frame: Optional[UploadFile] = File(None),
):
    """Enqueue a generation. JSON body for text-to-video; multipart/form-data
    (with a `request` JSON field + `first_frame`/`last_frame` files) for
    image-to-video. Returns 202 with the job resource."""
    api_key = ensure_key(api_key)
    settings = request.app.state.settings
    storage = request.app.state.storage
    db: JobStore = request.app.state.db

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        if not payload:
            raise HTTPException(400, "multipart requests must include a 'request' JSON field")
        try:
            req = VideoCreateRequest(**json.loads(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(422, f"invalid 'request' field: {exc}") from exc
    else:
        body = await request.json()
        req = VideoCreateRequest(**body)
        if first_frame or last_frame:
            raise HTTPException(400, "first_frame/last_frame require multipart/form-data")

    kind = "image_to_video" if first_frame else "text_to_video"
    if last_frame and not first_frame:
        raise HTTPException(400, "last_frame requires first_frame")

    # Admission control.
    if db.count(("queued", "running")) >= settings.max_queue_size:
        raise HTTPException(http_status.HTTP_429_TOO_MANY_REQUESTS, "queue is full")
    if db.count_for_key(api_key) >= settings.max_active_per_key:
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            f"per-key active job limit reached ({settings.max_active_per_key})",
        )

    job_id = secrets.token_hex(8)
    seed = req.seed if req.seed is not None else secrets.randbits(53)
    dims = h3.normalize_dimensions(req.width, req.height)
    if dims is None and kind == "text_to_video":
        dims = h3.resolution_for(req.aspect_ratio, req.megapixels)

    params = {
        "prompt": req.prompt,
        "duration_seconds": req.duration_seconds,
        "seed": seed,
        "aspect_ratio": req.aspect_ratio,
        "megapixels": req.megapixels,
        "width": req.width,
        "height": req.height,
        "effective_width": dims[0] if dims else None,
        "effective_height": dims[1] if dims else None,
        "has_last_frame": last_frame is not None,
    }

    if first_frame is not None:
        try:
            data = await first_frame.read()
            storage.save_upload(job_id, "first_frame", data, first_frame.content_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if last_frame is not None:
        try:
            data = await last_frame.read()
            storage.save_upload(job_id, "last_frame", data, last_frame.content_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    db.create(job_id, api_key, kind, params)
    job = db.get(job_id)
    job["queue_position"] = db.queue_ahead(job_id)
    log.info("enqueued %s job %s (key=%s)", kind, job_id, api_key[:8])
    return job_to_api(request, job)


@router.get("", response_model=VideoListResponse)
async def list_videos(
    request: Request,
    api_key: str = Depends(require_api_key),
    status: Optional[str] = Query(None, description="queued|running|succeeded|failed|cancelled"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    api_key = ensure_key(api_key)
    db: JobStore = request.app.state.db
    jobs, total = db.list(status=status, limit=limit, offset=offset)
    return VideoListResponse(
        jobs=[job_to_api(request, j) for j in jobs],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{job_id}", response_model=VideoJob)
async def get_video(
    request: Request, job_id: str, api_key: str = Depends(require_api_key)
):
    api_key = ensure_key(api_key)
    job = _db(request).get(job_id)
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "job not found")
    if job["status"] == "queued":
        job["queue_position"] = _db(request).queue_ahead(job_id)
    return job_to_api(request, job)


@router.delete("/{job_id}", response_model=VideoJob)
async def cancel_video(
    request: Request, job_id: str, api_key: str = Depends(require_api_key)
):
    api_key = ensure_key(api_key)
    db: JobStore = _db(request)
    job = db.get(job_id)
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "job not found")
    await request.app.state.worker.cancel(job)
    return job_to_api(request, db.get(job_id))


@router.get("/{job_id}/events")
async def stream_events(
    request: Request, job_id: str, api_key: str = Depends(require_api_key)
):
    """Server-Sent Events stream of job status/progress updates."""
    api_key = ensure_key(api_key)
    db: JobStore = _db(request)
    job = db.get(job_id)
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "job not found")
    bus = request.app.state.bus

    async def gen():
        queue = bus.subscribe(job_id)
        try:
            yield f"data: {json.dumps(status_payload(job))}\n\n"
            if job["status"] in POLL_DONE:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("status") in POLL_DONE:
                    return
        finally:
            bus.unsubscribe(job_id, queue)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/{job_id}/content")
async def download_video(
    request: Request,
    job_id: str,
    token: Optional[str] = Query(None),
    expires: Optional[int] = Query(None),
    api_key: str = Depends(require_api_key),
):
    """Download the generated MP4. Accepts either a valid API key or the
    signed `token`/`expires` pair embedded in the job's `links.content`."""
    settings = request.app.state.settings
    if settings.auth_enabled:
        authorized = api_key is not None
        if not authorized and token and expires:
            authorized = verify_signed_token(settings, job_id, expires, token)
        if not authorized:
            raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "not authorized")

    job = _db(request).get(job_id)
    if not job:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "job not found")
    if job["status"] != "succeeded" or not job.get("result_path"):
        raise HTTPException(http_status.HTTP_409_CONFLICT, f"job status is '{job['status']}'")
    path = request.app.state.storage.result_path(job_id)
    if not path.exists():
        raise HTTPException(http_status.HTTP_410_GONE, "result file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")



