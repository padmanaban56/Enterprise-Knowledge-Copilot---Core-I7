"""
ingestion/job_tracker.py — lightweight job tracking for /ingest/file & /ingest/bulk

Provides:
  - IngestJob       : dataclass describing one file's ingestion job
  - JobStatus       : status enum (queued/processing/completed/failed/cancelled/...)
  - INGEST_STAGES   : ordered (stage_name, progress_percent) pairs used by the
                       admin UI's progress bar
  - JobTracker      : process-local, in-memory registry of jobs
  - get_job_tracker : module-level singleton accessor
  - JobCancelled    : raised internally by main.py's ingestion wrapper when a
                       cancellation request is observed between stages

Design notes
------------
This is intentionally infrastructure-free (no Redis/Postgres) — a single
process-local dict guarded by an `asyncio.Lock`. That's sufficient for a
single-instance admin ingestion UI with progress polling and cancellation.
If a multi-worker deployment needs cross-process job visibility later, the
public API below (create_job / get / list_jobs / set_stage / mark_* /
request_cancel / is_cancel_requested) can be re-implemented on top of Redis
without changing any caller in api/main.py or the frontend.

Cancellation model
------------------
Cancellation is COOPERATIVE: `request_cancel()` sets `cancel_requested=True`
on the job. The ingestion wrapper in api/main.py checks
`is_cancel_requested()` BETWEEN the existing ingestion stages (parsing,
chunking, enriching, indexing, ...) and stops early if requested — it never
interrupts a single library call mid-flight (e.g. `vector_store.upsert_chunks`
always runs to completion once started), so no partial/corrupt writes are
introduced. For a job still in QUEUED state (its background task hasn't
started any work yet), `request_cancel()` also cancels the underlying
`asyncio.Task` directly for an instant response.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    CANCELLING = "cancelling"   # cancel requested, wrapper hasn't observed it yet
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}


# Ordered ingestion stages -> progress percentage once that stage STARTS.
# Purely informational (drives the admin UI progress bar) — the wrapper in
# api/main.py calls `set_stage()` with these names at the corresponding
# points in the existing (unmodified) ingestion flow.
INGEST_STAGES: List[tuple] = [
    ("queued",            0),
    ("saving_upload",     5),
    ("parsing_document",  15),
    ("classifying",       20),
    ("chunking",          35),
    ("enriching_chunks",  55),
    ("extracting_images", 70),
    ("redacting_pii",     80),
    ("indexing",          92),
    ("finalizing",        98),
    ("completed",         100),
]
_STAGE_PROGRESS: Dict[str, int] = dict(INGEST_STAGES)


class JobCancelled(Exception):
    """Raised by api/main.py's ingestion wrapper when it observes
    `cancel_requested=True` between stages, to unwind cleanly (close temp
    files, etc.) before the JobTracker marks the job CANCELLED."""
    pass


@dataclass
class IngestJob:
    job_id: str
    filename: str
    status: str = JobStatus.QUEUED.value
    stage: str = "queued"
    progress: int = 0
    message: str = ""
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Groups jobs created by a single /ingest/bulk call
    batch_id: Optional[str] = None
    # Free-form request context for display (department, repository, ...)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JobTracker:
    """Process-local registry of ingestion jobs (single-instance only)."""

    # Cap retained finished jobs so a long-lived process doesn't accumulate
    # unbounded history.
    MAX_FINISHED_JOBS = 200

    def __init__(self):
        self._jobs: Dict[str, IngestJob] = {}
        self._tasks: Dict[str, "asyncio.Task"] = {}
        self._lock = asyncio.Lock()

    # ── Job lifecycle ────────────────────────────────────────────────────────
    async def create_job(self, filename: str, batch_id: Optional[str] = None,
                          meta: Optional[Dict[str, Any]] = None) -> IngestJob:
        job = IngestJob(job_id=str(uuid.uuid4()), filename=filename,
                         batch_id=batch_id, meta=meta or {})
        async with self._lock:
            self._jobs[job.job_id] = job
            self._evict_old_jobs_locked()
        return job

    def register_task(self, job_id: str, task: "asyncio.Task"):
        """Store a reference to the background asyncio.Task for this job so
        a still-QUEUED job can be cancelled instantly. Not part of the
        serialized job state."""
        self._tasks[job_id] = task

    async def get(self, job_id: str) -> Optional[IngestJob]:
        async with self._lock:
            job = self._jobs.get(job_id)
            return IngestJob(**asdict(job)) if job else None

    async def list_jobs(self, batch_id: Optional[str] = None,
                         limit: int = 50) -> List[IngestJob]:
        async with self._lock:
            jobs = [IngestJob(**asdict(j)) for j in self._jobs.values()]
        if batch_id:
            jobs = [j for j in jobs if j.batch_id == batch_id]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    # ── Progress updates (called by api/main.py's ingestion wrapper) ────────
    async def set_stage(self, job_id: str, stage: str, message: str = ""):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.stage = stage
            job.progress = _STAGE_PROGRESS.get(stage, job.progress)
            if message:
                job.message = message
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.PROCESSING.value
            job.updated_at = time.time()

    async def set_progress(self, job_id: str, progress: int, message: str = ""):
        """Directly set a job's progress percentage (0-100), independent of
        the named INGEST_STAGES table. Used by batch/row-based jobs (e.g.
        ticket CSV ingestion) where progress is computed from row counts
        rather than discrete pipeline stages."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0, min(100, progress))
            if message:
                job.message = message
            if job.status == JobStatus.QUEUED.value:
                job.status = JobStatus.PROCESSING.value
            job.updated_at = time.time()

    async def mark_completed(self, job_id: str, result: Dict[str, Any]):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = JobStatus.COMPLETED.value
            job.stage = "completed"
            job.progress = 100
            job.result = result
            job.message = "Completed"
            job.updated_at = time.time()
        self._tasks.pop(job_id, None)

    async def mark_failed(self, job_id: str, error: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = JobStatus.FAILED.value
            job.error = error
            job.message = f"Failed: {error}"
            job.updated_at = time.time()
        self._tasks.pop(job_id, None)

    async def mark_cancelled(self, job_id: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = JobStatus.CANCELLED.value
            job.message = "Cancelled by user"
            job.updated_at = time.time()
        self._tasks.pop(job_id, None)

    # ── Cancellation ─────────────────────────────────────────────────────────
    async def request_cancel(self, job_id: str) -> bool:
        """Flag a job for cancellation. Returns False if the job is unknown
        or already in a terminal state.

        If the job's background task hasn't started any work yet (status is
        still QUEUED), also cancels the asyncio.Task directly for an instant
        response. Otherwise the ingestion wrapper observes
        `cancel_requested` at the next stage boundary and unwinds via
        `JobCancelled`.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in _TERMINAL_STATUSES:
                return False
            job.cancel_requested = True
            still_queued = job.status == JobStatus.QUEUED.value
            job.status = JobStatus.CANCELLING.value
            job.updated_at = time.time()

        if still_queued:
            task = self._tasks.get(job_id)
            if task and not task.done():
                task.cancel()
        return True

    async def is_cancel_requested(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.cancel_requested)

    # ── Housekeeping ─────────────────────────────────────────────────────────
    def _evict_old_jobs_locked(self):
        """Drop the oldest finished jobs once MAX_FINISHED_JOBS is exceeded.
        Must be called while holding self._lock."""
        finished = [j for j in self._jobs.values() if j.status in _TERMINAL_STATUSES]
        overflow = len(finished) - self.MAX_FINISHED_JOBS
        if overflow <= 0:
            return
        finished.sort(key=lambda j: j.updated_at)
        for job in finished[:overflow]:
            self._jobs.pop(job.job_id, None)
            self._tasks.pop(job.job_id, None)


# ── Module-level singleton ────────────────────────────────────────────────────
_tracker: Optional[JobTracker] = None


def get_job_tracker() -> JobTracker:
    global _tracker
    if _tracker is None:
        _tracker = JobTracker()
    return _tracker
