"""Async client for a headless ComfyUI backend (HTTP + WebSocket progress)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import quote, urlparse

import httpx
import websockets

log = logging.getLogger(__name__)

ProgressCallback = Callable[[Optional[float], str], Awaitable[None]]


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, read=600.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    # -- health -----------------------------------------------------------

    async def ping(self) -> bool:
        try:
            r = await self._http.get("/internal/queue", timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def system_stats(self) -> dict:
        r = await self._http.get("/system_stats")
        r.raise_for_status()
        return r.json()

    async def interrupt(self) -> None:
        r = await self._http.post("/interrupt")
        r.raise_for_status()

    # -- job submission ----------------------------------------------------

    async def submit(self, workflow: dict) -> str:
        """POST an API-format workflow to /prompt; returns the prompt_id."""
        r = await self._http.post(
            "/prompt", json={"prompt": workflow, "client_id": self.client_id}
        )
        if r.status_code != 200:
            raise ComfyError(f"comfy rejected the workflow: {r.text[:2000]}")
        data = r.json()
        errors = data.get("node_errors") or {}
        if errors:
            raise ComfyError(f"comfy node errors: {json.dumps(errors)[:2000]}")
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"comfy did not return a prompt_id: {r.text[:500]}")
        return prompt_id

    async def upload_image(self, data: bytes, filename: str) -> str:
        """Upload an input image; returns the ComfyUI input name."""
        r = await self._http.post(
            "/upload/image",
            files={"image": (filename, data)},
            data={"overwrite": "true"},
        )
        r.raise_for_status()
        name = r.json().get("name")
        if not name:
            raise ComfyError(f"image upload did not return a name: {r.text[:500]}")
        return name

    # -- progress + results --------------------------------------------------

    async def stream_progress(self, prompt_id: str, on_progress: ProgressCallback) -> None:
        """Follow execution progress over the WebSocket (with history-polling
        fallback). Returns normally on success; raises ComfyError on error.
        """
        try:
            await self._stream_progress_ws(prompt_id, on_progress)
        except (websockets.WebSocketException, OSError) as exc:
            log.warning("ws progress unavailable (%s); falling back to polling", exc)
            await self._poll_history(prompt_id)

    async def _stream_progress_ws(self, prompt_id: str, on_progress: ProgressCallback) -> None:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        uri = f"{scheme}://{parsed.netloc}/ws?clientId={self.client_id}"
        async with websockets.connect(uri, open_timeout=10, ping_interval=20) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                if isinstance(msg, bytes):
                    continue
                mtype = msg.get("type")
                data = msg.get("data") or {}
                pid = data.get("prompt_id")
                if pid and pid != prompt_id:
                    continue
                if mtype == "progress":
                    value, mx = data.get("value", 0), max(data.get("max", 1), 1)
                    await on_progress(value / mx, f"sampling {value}/{mx}")
                elif mtype == "executing":
                    node = data.get("node")
                    if node is not None:
                        await on_progress(None, f"executing node {node}")
                elif mtype == "execution_success":
                    return
                elif mtype == "execution_error":
                    raise ComfyError(
                        f"execution error in {data.get('node_type')}: "
                        f"{data.get('exception_message')}"
                    )
                elif mtype == "execution_interrupted":
                    raise ComfyError("execution interrupted")
                # 'status', 'execution_cached' etc. are ignored.

    async def _poll_history(self, prompt_id: str) -> None:
        for _ in range(3600):  # up to 1h at 1s cadence
            history = await self.get_history(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    messages = "; ".join(
                        m.get("message", "") for m in status.get("messages", [])
                    )
                    raise ComfyError(f"execution error: {messages}")
                return
            await asyncio.sleep(1.0)

    async def get_history(self, prompt_id: str) -> dict:
        r = await self._http.get(f"/history/{quote(prompt_id)}")
        r.raise_for_status()
        return r.json().get(prompt_id, {})

    async def find_video_output(self, prompt_id: str, node_key: str) -> Optional[dict]:
        history = await self.get_history(prompt_id)
        outputs = history.get("outputs") or {}
        node_out = outputs.get(node_key) or {}
        videos = node_out.get("videos") or node_out.get("gifs") or []
        return videos[0] if videos else None

    async def download_output(self, entry: dict, dest: Path) -> Path:
        params = {
            "filename": entry["filename"],
            "subfolder": entry.get("subfolder", ""),
            "type": entry.get("type", "output"),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with self._http.stream("GET", "/view", params=params) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
        return dest
