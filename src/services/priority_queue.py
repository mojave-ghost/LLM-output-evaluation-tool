import heapq
import time
from typing import Optional

from ..models.eval_job import EvalJob
from ..models.job_status import JobStatus


class PriorityQueue:
    """Min-heap priority queue for EvalJob scheduling.

    Heap entries are tuples: (-priority, timestamp, job) so that higher
    priority values are popped first, with FIFO ordering within the same
    priority tier.

    Matches the PriorityQueue class from the UML class diagram.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._heap: list[tuple] = []
        self.max_workers: int = max_workers
        self.running: int = 0

    def push(self, job: EvalJob) -> None:
        """Add a job to the heap. Lower priority value is processed first.

        SRS FR-005: 0 = urgent, 1 = standard, 2 = background.
        Python's heapq is a min-heap, so the smallest tuple pops first.
        We store (priority, timestamp, job) directly — no negation needed.
        """
        entry = (job.priority, time.monotonic(), job)
        heapq.heappush(self._heap, entry)
        job.enqueue()

    def pop(self) -> Optional[EvalJob]:
        """Remove and return the highest-priority job, or None if empty."""
        while self._heap:
            _, _, job = heapq.heappop(self._heap)
            if job.status == JobStatus.QUEUED:
                job.status = JobStatus.PROCESSING
                self.running += 1
                return job
        return None

    def __len__(self) -> int:
        return len(self._heap)
