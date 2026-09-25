"""End-to-end API tests against the FastAPI app with a fake Comfy backend."""
from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

POLL = {"queued", "running"}


async def wait_terminal(client: AsyncClient, job_id: str, timeout: float = 10.0,
                        headers: dict | None = None):
    async def _poll():
        while True:
            r = await client.get(f"/v1/videos/{job_id}", headers=headers)
            if r.status_code == 200 and r.json()["status"] not in POLL:
                return r.json()
            await asyncio.sleep(0.05)
    return await asyncio.wait_for(_poll(), timeout)


@pytest.mark.asyncio
async def test_t2v_full_flow(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/videos",
            json={"prompt": "a neon city at night", "duration_seconds": 2.0, "seed": 99},
        )
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] in POLL
        assert job["seed"] == 99
        assert job["effective_frames"] == 56
        assert job["effective_width"] == 640
        assert job["links"]["self"].endswith(job["id"])

        final = await wait_terminal(client, job["id"])
        assert final["status"] == "succeeded", final
        assert final["progress"] == 1.0
        assert "content" in final["links"]

        # The submitted workflow carried the prompt/seed.
        wf = app.state.comfy.submitted[0]
        h3node = next(
            v for v in wf.values() if v["class_type"] == "MiniMaxH3ImageToVideo"
        )
        assert h3node["inputs"]["prompt"] == "a neon city at night"
        assert wf["s15"]["inputs"]["noise_seed"] == 99

        # Result downloads via the signed URL.
        r = await client.get(final["links"]["content"])
        assert r.status_code == 200
        assert r.content == b"FAKE_VIDEO_BYTES"
        assert r.headers["content-type"].startswith("video/mp4")


@pytest.mark.asyncio
async def test_i2v_multipart_flow(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = json.dumps({"prompt": "animate the barrier", "duration_seconds": 3.0})
        r = await client.post(
            "/v1/videos",
            files={
                "request": (None, payload, "application/json"),
                "first_frame": ("first.png", b"PNGDATA", "image/png"),
                "last_frame": ("last.png", b"PNGDATA2", "image/png"),
            },
        )
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["kind"] == "image_to_video"
        final = await wait_terminal(client, job["id"])
        assert final["status"] == "succeeded"
        assert len(app.state.comfy.uploads) == 2
        wf = app.state.comfy.submitted[0]
        h3node = next(
            v for v in wf.values() if v["class_type"] == "MiniMaxH3ImageToVideo"
        )
        assert h3node["inputs"]["first_frame"] == ["n136", 0]
        assert h3node["inputs"]["last_frame"] == ["n_last_frame", 0]


@pytest.mark.asyncio
async def test_i2v_requires_first_frame(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = json.dumps({"prompt": "x"})
        r = await client.post(
            "/v1/videos",
            files={
                "request": (None, payload, "application/json"),
                "last_frame": ("last.png", b"PNG", "image/png"),
            },
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_and_list(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/videos", json={"prompt": "demo"})
        job_id = r.json()["id"]
        await wait_terminal(client, job_id)
        r = await client.get(f"/v1/videos/{job_id}")
        assert r.status_code == 200
        r = await client.get("/v1/videos?status=succeeded")
        assert any(j["id"] == job_id for j in r.json()["jobs"])
        r = await client.get("/v1/videos/does-not-exist")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_queued(app):
    # Freeze queue processing so the job stays queued long enough to cancel.
    app.state.db.claim_next = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/videos", json={"prompt": "to be cancelled"})
        job_id = r.json()["id"]
        r = await client.delete(f"/v1/videos/{job_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_auth_enforced(tmp_path, fake_comfy, monkeypatch):
    from app.config import Settings
    from app import main

    settings = Settings(
        comfy_url="http://127.0.0.1:8188", api_keys="secret-key-1",
        data_dir=tmp_path, poll_interval_seconds=0.01, _env_file=None,
    )
    monkeypatch.setattr(main, "ComfyClient", lambda _url: fake_comfy)
    application = main.create_app(settings)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        headers = {"X-API-Key": "secret-key-1"}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/videos", json={"prompt": "x"})
            assert r.status_code == 401
            r = await client.post(
                "/v1/videos", json={"prompt": "x"}, headers={"X-API-Key": "wrong"}
            )
            assert r.status_code == 401
            r = await client.post(
                "/v1/videos", json={"prompt": "x"}, headers=headers
            )
            assert r.status_code == 202
            job = r.json()
            final = await wait_terminal(client, job["id"], headers=headers)
            # Signed content URL works WITHOUT an API key (frontend <img> use).
            r = await client.get(final["links"]["content"])
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_per_key(tmp_path, fake_comfy, monkeypatch):
    from app.config import Settings
    from app import main

    settings = Settings(
        comfy_url="http://127.0.0.1:8188", api_keys="k1",
        data_dir=tmp_path, max_active_per_key=1, max_queue_size=16,
        poll_interval_seconds=100.0, _env_file=None,  # worker effectively frozen
    )
    monkeypatch.setattr(main, "ComfyClient", lambda _url: fake_comfy)
    application = main.create_app(settings)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-API-Key": "k1"}
            r = await client.post("/v1/videos", json={"prompt": "a"}, headers=headers)
            assert r.status_code == 202
            r = await client.post("/v1/videos", json={"prompt": "b"}, headers=headers)
            assert r.status_code == 429


@pytest.mark.asyncio
async def test_health_and_metrics(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["comfy_backend"] == "up"
        r = await client.get("/metrics")
        assert r.status_code == 200
        assert r.json()["gpu"][0]["name"] == "Fake GPU"
        r = await client.get("/v1/models")
        assert r.status_code == 200
        assert r.json()["steps"] == 8
