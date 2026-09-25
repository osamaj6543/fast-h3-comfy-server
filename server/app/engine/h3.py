"""FastH3 model constants and server-side helpers.

Mirrors the constraints documented for MiniMax H3 / FastVideo FastH3:
* native canvas: 768px short edge, capped at 768x1344, multiples of 32
* duration snaps to the model's 17-frame-per-block grid (17k+5) at 24fps
* exactly 8 sampling steps (DMD2 distilled checkpoint)
"""

STEPS = 8
FPS = 24
SHORT_EDGE_NATIVE = 768
MAX_DIMENSION = 1344
MIN_DIMENSION = 320
MULTIPLE = 32

# Aspect ratios exposed by the API (server computes the pixel resolution).
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "21:9": (21, 9),
    "9:21": (9, 21),
}


def snap_to_multiple(value: int, multiple: int = MULTIPLE) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def clamp_dimension(value: int) -> int:
    return max(MIN_DIMENSION, min(MAX_DIMENSION, value))


def resolution_for(aspect_ratio: str, megapixels: float) -> tuple[int, int]:
    """Compute a pixel resolution for an aspect ratio + megapixel budget,
    snapped to the H3 constraints (multiples of 32, capped at 1344)."""
    rw, rh = ASPECT_RATIOS[aspect_ratio]
    total = megapixels * 1_000_000
    w = snap_to_multiple(int(round((total * rw / rh) ** 0.5)))
    h = snap_to_multiple(int(round((total * rh / rw) ** 0.5)))
    return clamp_dimension(w), clamp_dimension(h)


def normalize_dimensions(width: int | None, height: int | None) -> tuple[int, int] | None:
    """Snap explicit width/height to the H3 grid; None if not provided."""
    if width is None and height is None:
        return None
    if width is None or height is None:
        raise ValueError("width and height must be provided together")
    return clamp_dimension(snap_to_multiple(width)), clamp_dimension(snap_to_multiple(height))


def frames_for_duration(duration_seconds: float) -> int:
    """Replicates the workflow's ComfyMathExpression so the API can report
    the effective frame count: snap frame count to the 17k+5 grid at 24fps.
    """
    n = max(5, round(duration_seconds * FPS))
    return n + (5 - (n % 17)) % 17


def duration_for_frames(frames: int) -> float:
    return frames / FPS
