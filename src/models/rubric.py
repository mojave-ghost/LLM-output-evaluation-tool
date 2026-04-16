import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Rubric:
    owner_id: uuid.UUID
    name: str
    is_default: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def validate_weights(self) -> bool:
        """Return True if all associated RubricDimension weights sum to 1.0."""
        raise NotImplementedError
