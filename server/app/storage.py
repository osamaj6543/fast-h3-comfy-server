"""Local output storage: uploaded inputs and generated videos."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class Storage:
    def __init__(self, outputs_dir: Path, uploads_dir: Path):
        self.outputs_dir = outputs_dir
        self.uploads_dir = uploads_dir
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, job_id: str, role: str, data: bytes, content_type: str) -> Path:
        ext = ALLOWED_IMAGE_TYPES.get((content_type or "").split(";")[0].strip().lower())
        if ext is None:
            raise ValueError(f"unsupported image content type: {content_type}")
        path = self.uploads_dir / f"{job_id}_{role}{ext}"
        path.write_bytes(data)
        return path

    def upload_path(self, job_id: str, role: str) -> Path | None:
        matches = sorted(self.uploads_dir.glob(f"{job_id}_{role}.*"))
        return matches[0] if matches else None

    def result_path(self, job_id: str) -> Path:
        return self.outputs_dir / f"{job_id}.mp4"

    def result_size(self, job_id: str) -> int | None:
        path = self.result_path(job_id)
        return path.stat().st_size if path.exists() else None

    def remove_uploads(self, job_id: str) -> None:
        for path in self.uploads_dir.glob(f"{job_id}_*"):
            try:
                path.unlink()
            except OSError:  # pragma: no cover
                log.warning("could not remove upload %s", path)
