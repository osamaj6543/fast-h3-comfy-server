"""SQLite-backed persistent job store.

A single worker pulls jobs FIFO via claim_next(); the store survives server
restarts (running jobs are requeued at startup so nothing is lost).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ACTIVE = ("queued", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    api_key       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    params        TEXT NOT NULL,
    prompt_id     TEXT,
    progress      REAL NOT NULL DEFAULT 0.0,
    queue_position INTEGER,
    status_message TEXT,
    error         TEXT,
    result_path   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(api_key);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: Path):
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        job = dict(row)
        job["params"] = json.loads(job["params"])
        return job

    def create(self, job_id: str, api_key: str, kind: str, params: dict) -> dict:
        now = _now()
        record = {
            "id": job_id,
            "api_key": api_key,
            "kind": kind,
            "status": "queued",
            "params": json.dumps(params),
            "prompt_id": None,
            "progress": 0.0,
            "queue_position": None,
            "status_message": None,
            "error": None,
            "result_path": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (id, api_key, kind, status, params, prompt_id, "
                "progress, queue_position, status_message, error, result_path, "
                "created_at, updated_at) VALUES (:id, :api_key, :kind, :status, "
                ":params, :prompt_id, :progress, :queue_position, :status_message, "
                ":error, :result_path, :created_at, :updated_at)",
                record,
            )
        return self.get(job_id)

    def list(self, *, status: Optional[str] = None, api_key: Optional[str] = None,
             limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        clauses, args = [], []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if api_key:
            clauses.append("api_key = ?")
            args.append(api_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM jobs {where}", args
            ).fetchone()[0]
        return [self._row_to_dict(r) for r in rows], total

    def count(self, statuses: tuple[str, ...]) -> int:
        q = ",".join("?" * len(statuses))
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE status IN ({q})", statuses
            ).fetchone()[0]

    def count_for_key(self, api_key: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE api_key = ? AND status IN (?,?)",
                (api_key, *_ACTIVE),
            ).fetchone()[0]

    def claim_next(self) -> Optional[dict]:
        """Atomically move the oldest queued job to running and return it."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' "
                "ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
        return self.get(row["id"])

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = job_id
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE id = :id", fields)

    def requeue_running(self) -> int:
        """After a restart, requeue jobs that were mid-flight."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'queued', progress = 0.0, updated_at = ? "
                "WHERE status = 'running'",
                (_now(),),
            )
            return cur.rowcount

    def queue_ahead(self, job_id: str) -> Optional[int]:
        job = self.get(job_id)
        if not job or job["status"] != "queued":
            return None
        created = job["created_at"]
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND created_at < ?",
                (created,),
            ).fetchone()[0]

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None
