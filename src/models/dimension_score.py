import uuid
from dataclasses import dataclass, field


@dataclass
class DimensionScore:
    result_id: uuid.UUID
    dimension_id: uuid.UUID
    score: int  # 1–5
    rationale: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if not (1 <= self.score <= 5):
            raise ValueError(f"score must be between 1 and 5; got {self.score}")
