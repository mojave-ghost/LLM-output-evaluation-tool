import uuid
from dataclasses import dataclass, field


@dataclass
class RubricDimension:
    rubric_id: uuid.UUID
    name: str
    description: str
    weight: float
    id: uuid.UUID = field(default_factory=uuid.uuid4)
