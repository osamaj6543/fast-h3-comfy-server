"""API request/response schemas."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.engine import h3

AspectRatio = Literal["1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "21:9", "9:21"]

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JobKind = Literal["text_to_video", "image_to_video"]


class VideoCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    duration_seconds: float = Field(default=5.0, ge=1.0, le=10.0)
    seed: Optional[int] = Field(default=None, ge=0, le=2**53 - 1)
    aspect_ratio: AspectRatio = "1:1"
    megapixels: float = Field(default=0.4, ge=0.05, le=0.4)
    width: Optional[int] = Field(default=None, ge=64, le=1344)
    height: Optional[int] = Field(default=None, ge=64, le=1344)

    @model_validator(mode="after")
    def _dims_together(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        return self


class VideoJob(BaseModel):
    id: str
    kind: JobKind
    status: JobStatus
    prompt_preview: str
    created_at: datetime
    updated_at: datetime
    duration_seconds: float
    effective_frames: int
    effective_width: Optional[int] = None
    effective_height: Optional[int] = None
    seed: int
    progress: float = Field(ge=0.0, le=1.0)
    queue_position: Optional[int] = None
    error: Optional[str] = None
    links: dict[str, str] = {}


class VideoListResponse(BaseModel):
    jobs: list[VideoJob]
    total: int
    limit: int
    offset: int


class ModelCard(BaseModel):
    id: str = "fastvideo-fasth3-8step"
    name: str = "FastVideo FastH3 (MiniMax H3, 8-step distilled)"
    audio: bool = True
    steps: int = h3.STEPS
    fps: int = h3.FPS
    capabilities: list[str] = ["text_to_video", "image_to_video", "first_last_frame"]
    aspect_ratios: list[str] = list(h3.ASPECT_RATIOS)
    duration_seconds: dict = {"min": 1.0, "max": 10.0, "grid": "17k+5 frames @ 24fps"}
    resolution: dict = {
        "multiple": h3.MULTIPLE,
        "min": h3.MIN_DIMENSION,
        "max": h3.MAX_DIMENSION,
        "native_short_edge": h3.SHORT_EDGE_NATIVE,
    }


def safe_preview(prompt: str, limit: int = 140) -> str:
    prompt = " ".join(prompt.split())
    return prompt[: limit - 1] + "…" if len(prompt) > limit else prompt
