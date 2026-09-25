"""Single-GPU job worker: pulls jobs FIFO and drives ComfyUI to completion."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from app.db import JobStore
from app.engine import templates
from app.engine.comfy_client import ComfyClient, ComfyError
from app.engine.templates import TemplateError
from app.events import EventBus
from app.serialize import status_payload
from app.storage import Storage

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, db: JobStore, comfy: ComfyClient,
                 storage: Storage, bus: EventBus):
        self.settings = settings
        self.db = db
        self.comfy = comfy
        self.storage = storage
        self.bus = bus
        self._task: Optional[asyncio.Task] = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        requeued = self.db.requeue_running()
        if requeued:
            log.info("requeued %d interrupted job(s) after restart", requeued)
        self._task = asyncio.create_task(self._loop(), name="job-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- internals -----------------------------------------------------------

    def _publish(self, job: Optional[dict]) -> None:
        if job:
            self.bus.publish(job["id"], status_payload(job))

    async def _loop(self) -> None:
        while True:
            try:
                job = self.db.claim_next()
                if job is None:
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must never die
                log.exception("worker iteration failed")
                await asyncio.sleep(2.0)

    async def _process(self, job: dict) -> None:
        job_id = job["id"]
        try:
            await self._run_generation(job)
        except (ComfyError, TemplateError, OSError, ValueError) as exc:
            log.error("job %s failed: %s", job_id, exc)
            updated = self.db.get(job_id)
            if updated and updated["status"] != "cancelled":
                self.db.update(job_id, status="failed", error=str(exc)[:2000])
                self._publish(self.db.get(job_id))
        finally:
            self.storage.remove_uploads(job_id)

    async def _run_generation(self, job: dict) -> None:
        db = self.db
        job_id = job["id"]
        params = job["params"]
        kind = job["kind"]

        # Upload input frames to ComfyUI for image-to-video jobs.
        first_frame = last_frame = None
        if kind == "image_to_video":
            first_frame = await self._upload_frame(job_id, "first_frame")
            if params.get("has_last_frame"):
                last_frame = await self._upload_frame(job_id, "last_frame")

        workflow = templates.build_workflow(
            kind,
            job_id=job_id,
            prompt=params["prompt"],
            duration_seconds=params["duration_seconds"],
            seed=params["seed"],
            width=params.get("width"),
            height=params.get("height"),
            aspect_ratio=params.get("aspect_ratio", "1:1"),
            megapixels=params.get("megapixels", 0.4),
            first_frame=first_frame,
            last_frame=last_frame,
        )
        meta = templates._load(
            "fasth3_t2v" if kind == "text_to_video" else "fasth3_i2v"
        )[1]
        save_key = meta["save_video"]

        if not await self.comfy.ping():
            raise ComfyError("ComfyUI backend is not reachable")

        prompt_id = await self.comfy.submit(workflow)
        db.update(job_id, prompt_id=prompt_id, status_message="submitted to ComfyUI")
        self._publish(db.get(job_id))

        async def on_progress(fraction, message: str) -> None:
            fields = {"status_message": message}
            if fraction is not None:
                fields["progress"] = fraction
            db.update(job_id, **fields)
            current = db.get(job_id)
            if current and current["status"] == "cancelled":
                raise ComfyError("execution interrupted")  # abort the stream
            self._publish(current)

        await self.comfy.stream_progress(prompt_id, on_progress)

        # A cancel request may have landed while the stream was winding down.
        current = db.get(job_id)
        if current and current["status"] == "cancelled":
            return

        entry = await self.comfy.find_video_output(prompt_id, save_key)
        if entry is None:
            raise ComfyError(f"no video output found for job {job_id}")

        dest = self.storage.result_path(job_id)
        await self.comfy.download_output(entry, dest)
        db.update(
            job_id, status="succeeded", progress=1.0,
            status_message="done", result_path=str(dest),
        )
        self._publish(db.get(job_id))
        log.info("job %s completed -> %s", job_id, dest)

    async def _upload_frame(self, job_id: str, role: str) -> str:
        path: Optional[Path] = self.storage.upload_path(job_id, role)
        if path is None:
            raise ValueError(f"missing {role} upload for job {job_id}")
        return await self.comfy.upload_image(path.read_bytes(), path.name)

    # -- cancellation ---------------------------------------------------------

    async def cancel(self, job: dict) -> bool:
        """Mark a job cancelled; interrupt ComfyUI if it is mid-flight."""
        status = job["status"]
        if status in ("succeeded", "failed", "cancelled"):
            return False
        self.db.update(job["id"], status="cancelled", status_message="cancelled")
        self._publish(self.db.get(job["id"]))
        if status == "running":
            try:
                await self.comfy.interrupt()
            except Exception:  # noqa: BLE001
                log.warning("comfy interrupt failed for %s", job["id"], exc_info=True)
        return True
