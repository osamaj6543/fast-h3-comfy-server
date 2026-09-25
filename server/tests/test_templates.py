"""Unit tests for the FastH3 workflow template parameterization."""
from __future__ import annotations

import pytest

from app.engine import h3, templates


def test_frames_grid():
    assert h3.frames_for_duration(5) == 124  # 17*7+5
    assert h3.frames_for_duration(2) == 56   # 17*3+5
    assert h3.frames_for_duration(1) == 39   # 17*2+5
    for d in (1.5, 3.0, 6.7, 10.0):
        f = h3.frames_for_duration(d)
        assert f % 17 == 5  # every duration lands on the 17k+5 grid


def test_resolution_snapping():
    w, h = h3.resolution_for("16:9", 0.4)
    assert w % 32 == 0 and h % 32 == 0
    assert w <= 1344 and h <= 1344
    w, h = h3.normalize_dimensions(770, 1000)
    assert (w, h) == (768, 992)


def test_normalize_requires_both():
    with pytest.raises(ValueError):
        h3.normalize_dimensions(768, None)


def test_t2v_workflow():
    wf = templates.build_workflow(
        "text_to_video", job_id="j1", prompt="a cat DJ",
        duration_seconds=5.0, seed=42, aspect_ratio="9:16", megapixels=0.4,
    )
    h3node = next(v for v in wf.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    assert h3node["inputs"]["prompt"] == "a cat DJ"
    assert h3node["inputs"]["width"] == 480
    assert h3node["inputs"]["height"] == 832
    assert "n143" not in wf  # ResolutionSelector dropped
    noise = next(v for v in wf.values() if v["class_type"] == "RandomNoise")
    assert noise["inputs"]["noise_seed"] == 42
    assert noise["inputs"]["control_after_generate"] == "fixed"
    sched = next(v for v in wf.values() if v["class_type"] == "BasicScheduler")
    assert sched["inputs"]["steps"] == 8
    save = next(v for v in wf.values() if v["class_type"] == "SaveVideo")
    assert save["inputs"]["filename_prefix"] == "jobs/j1"
    # links intact
    keys = set(wf)
    for v in wf.values():
        for val in v["inputs"].values():
            if isinstance(val, list):
                assert val[0] in keys


def test_i2v_workflow_with_last_frame():
    wf = templates.build_workflow(
        "image_to_video", job_id="j2", prompt="make it move",
        duration_seconds=3.0, seed=7, first_frame="in.png",
        last_frame="end.png", megapixels=0.3,
    )
    h3node = next(v for v in wf.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    assert h3node["inputs"]["first_frame"] == ["n136", 0]
    assert h3node["inputs"]["last_frame"] == ["n_last_frame", 0]
    assert wf["n_last_frame"]["inputs"]["image"] == "end.png"
    scale = next(v for v in wf.values() if v["class_type"] == "ImageScaleToTotalPixels")
    assert scale["inputs"]["megapixels"] == 0.3


def test_i2v_requires_first_frame():
    with pytest.raises(templates.TemplateError):
        templates.build_workflow(
            "image_to_video", job_id="j3", prompt="x",
            duration_seconds=2.0, seed=1,
        )


def test_i2v_explicit_dims():
    wf = templates.build_workflow(
        "image_to_video", job_id="j4", prompt="x",
        duration_seconds=2.0, seed=1, first_frame="a.png",
        width=768, height=768,
    )
    h3node = next(v for v in wf.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    assert h3node["inputs"]["width"] == 768
    assert h3node["inputs"]["height"] == 768
