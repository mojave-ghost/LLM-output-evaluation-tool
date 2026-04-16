import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class EvalResult:
    job_id: uuid.UUID
    rubric_id: uuid.UUID
    composite_score: float = 0.0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def compute_composite(self, dimension_scores: List["DimensionScore"], dimensions: List["RubricDimension"]) -> float:
        """Compute the weighted-average composite score and store it on self.

        dimension_scores: list of DimensionScore for this result.
        dimensions: list of RubricDimension (provides weights, matched by id).
        """
        weight_map = {d.id: d.weight for d in dimensions}
        total_weight = 0.0
        weighted_sum = 0.0
        for ds in dimension_scores:
            w = weight_map.get(ds.dimension_id, 0.0)
            weighted_sum += ds.score * w
            total_weight += w
        self.composite_score = weighted_sum / total_weight if total_weight else 0.0
        return self.composite_score

    def to_csv_row(self) -> str:
        """Return a single CSV-formatted row representing this result."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            str(self.id),
            str(self.job_id),
            str(self.rubric_id),
            f"{self.composite_score:.4f}",
            self.created_at.isoformat(),
        ])
        return output.getvalue().strip()
