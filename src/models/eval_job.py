import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .job_status import JobStatus


@dataclass
class EvalJob:
    owner_id: uuid.UUID
    prompt: str
    response_text: str
    response_hash: str
    rubric_id: uuid.UUID
    priority: int = 1  # 0 = urgent, 1 = standard, 2 = background (SRS FR-005)
    status: JobStatus = JobStatus.QUEUED
    retry_count: int = 0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if self.priority not in (0, 1, 2):
            raise ValueError(f"priority must be 0, 1, or 2; got {self.priority}")

    def enqueue(self) -> None:
        """Set status to QUEUED. Caller is responsible for pushing to PriorityQueue."""
        self.status = JobStatus.QUEUED
        self.updated_at = datetime.utcnow()

    def retry(self) -> None:
        """Increment retry_count and reset status to QUEUED for re-processing.

        Raises RuntimeError after 3 failed attempts, setting status to FAILED.
        """
        self.retry_count += 1
        self.updated_at = datetime.utcnow()
        if self.retry_count >= 3:
            self.status = JobStatus.FAILED
            raise RuntimeError(
                f"Job {self.id} permanently failed after {self.retry_count} attempts."
            )
        self.status = JobStatus.QUEUED
