"""Shared fixtures: isolated settings, fake ComfyUI client, app instance."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.config import Settings
from app.engine.comfy_client import ComfyError


class FakeComfyClient:
    """Implements the ComfyClient surface the worker relies on, without a GPU."""

    def __init__(self, fail_submit: bool = False):
        self.submitted: list[dict] = []
        self.uploads: list[str] = []
        self.interrupted = 0
        self.fail_submit = fail_submit

    async def close(self):
        pass

    async def ping(self):
        return True

    async def system_stats(self):
        return {"devices": [{"name": "Fake GPU", "type": "cuda",
                             "vram_total": 8, "vram_free": 4}]}

    async def interrupt(self):
        self.interrupted += 1

    async def submit(self, workflow: dict) -> str:
        if self.fail_submit:
            raise ComfyError("comfy rejected the workflow")
        self.submitted.append(copy.deepcopy(workflow))
        return f"fake-prompt-{len(self.submitted)}"

    async def upload_image(self, data: bytes, filename: str) -> str:
        self.uploads.append(filename)
        return filename

    async def stream_progress(self, prompt_id, on_progress):
        await on_progress(0.5, "sampling 4/8")
        await on_progress(1.0, "sampling 8/8")

    async def find_video_output(self, prompt_id: str, node_key: str):
        return {"filename": f"{prompt_id}.mp4", "subfolder": "jobs", "type": "output"}

    async def download_output(self, entry, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FAKE_VIDEO_BYTES")
        return dest


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        comfy_url="http://127.0.0.1:8188",
        api_keys="",
        data_dir=tmp_path,
        max_queue_size=16,
        max_active_per_key=4,
        poll_interval_seconds=0.01,
        _env_file=None,  # type: ignore[arg-type]
    )


@pytest.fixture
def fake_comfy():
    return FakeComfyClient()


@pytest_asyncio.fixture
async def app(settings, fake_comfy, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "ComfyClient", lambda _url: fake_comfy)
    application = main.create_app(settings)
    async with application.router.lifespan_context(application):
        yield application
