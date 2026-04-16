import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class User:
    email: str
    hashed_pw: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def register(self) -> None:
        """Persist this user to the database."""
        raise NotImplementedError

    def login(self) -> str:
        """Validate credentials and return a session token."""
        raise NotImplementedError
