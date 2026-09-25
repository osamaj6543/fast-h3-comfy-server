"""Parameterize the flattened FastH3 API-format workflow templates."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional

from app.engine import h3

TEMPLATES_DIR = Path(__file__).resolve().parent / "workflows"

_TEMPLATES: dict[str, dict] = {}
_META: dict[str, dict] = {}


def _load(name: str) -> tuple[dict, dict]:
    if name not in _TEMPLATES:
        _TEMPLATES[name] = json.loads(
            (TEMPLATES_DIR / f"{name}.api.json").read_text(encoding="utf-8")
        )
        _META[name] = json.loads(
            (TEMPLATES_DIR / f"{name}.meta.json").read_text(encoding="utf-8")
        )
    return _TEMPLATES[name], _META[name]


class TemplateError(ValueError):
    pass


def build_workflow(
    kind: str,
    *,
    job_id: str,
    prompt: str,
    duration_seconds: float,
    seed: int,
    width: Optional[int] = None,
    height: Optional[int] = None,
    aspect_ratio: str = "1:1",
    megapixels: float = 0.4,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
) -> dict:
    """Build a ready-to-submit ComfyUI API workflow for one generation job.

    kind: "text_to_video" or "image_to_video".
    first_frame/last_frame are ComfyUI input-image names (as returned by
    ComfyClient.upload_image) for image_to_video jobs.
    """
    name = "fasth3_t2v" if kind == "text_to_video" else "fasth3_i2v"
    template, meta = _load(name)
    wf = copy.deepcopy(template)

    noise = wf[meta["sampler_noise"]]
    h3node = wf[meta["h3_i2v"]]
    duration_node = wf[meta["duration_float"]]
    save_video = wf[meta["save_video"]]

    # Seed: pin the RNG so the API honors the requested seed exactly.
    noise["inputs"]["noise_seed"] = seed
    noise["inputs"]["control_after_generate"] = "fixed"

    # Prompt + duration (the math-expression node snaps to the 17k+5 grid).
    h3node["inputs"]["prompt"] = prompt
    duration_node["inputs"]["value"] = duration_seconds

    # Isolate outputs per job.
    save_video["inputs"]["filename_prefix"] = f"jobs/{job_id}"

    if kind == "text_to_video":
        dims = h3.normalize_dimensions(width, height) or h3.resolution_for(
            aspect_ratio, megapixels
        )
        # Set explicit dimensions and drop the ResolutionSelector node.
        h3node["inputs"]["width"], h3node["inputs"]["height"] = dims
        rs = meta.get("resolution_selector")
        if rs:
            del wf[rs]
    else:
        if not first_frame:
            raise TemplateError("image_to_video requires a first_frame")
        load_image = wf[meta["load_image"]]
        load_image["inputs"]["image"] = first_frame
        load_image["inputs"]["upload"] = "image"
        scale = next(
            v for v in wf.values() if v["class_type"] == "ImageScaleToTotalPixels"
        )
        scale["inputs"]["megapixels"] = megapixels
        dims = h3.normalize_dimensions(width, height)
        if dims:
            # Explicit dimensions override the image-derived size.
            h3node["inputs"]["width"], h3node["inputs"]["height"] = dims
        if last_frame:
            key = "n_last_frame"
            wf[key] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_frame, "upload": "image"},
            }
            h3node["inputs"]["last_frame"] = [key, 0]

    return wf
