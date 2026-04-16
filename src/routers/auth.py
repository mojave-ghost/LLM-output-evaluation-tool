"""Auth routes — POST /auth/register, POST /auth/login, POST /auth/refresh.

Requirements satisfied
----------------------
FR-020  bcrypt password hashing, cost factor >= 12
FR-021  JWT access token (15 min) + refresh token (7 days) on login
FR-022  get_current_user dependency — import and use in every other router
NFR-011 All DB access via SQLAlchemy ORM; no raw SQL string interpolation
NFR-023 No stack traces returned to the client
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import UserORM, get_db

# ---------------------------------------------------------------------------
# Configuration — read from environment, safe defaults for dev only
# ---------------------------------------------------------------------------

JWT_SECRET: str = os.environ.get("JWT_SECRET", "change-me-before-production")
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 15       # FR-021
REFRESH_TOKEN_EXPIRE_DAYS: int = 7          # FR-021
BCRYPT_ROUNDS: int = 12                     # FR-020: cost factor >= 12

# ---------------------------------------------------------------------------
# Password hashing — uses bcrypt directly (avoids passlib/bcrypt-4.x compat issues)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def _create_token(subject: str, token_type: str, expires_delta: timedelta) -> str:
    """Return a signed JWT with 'sub', 'type', and 'exp' claims."""
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {"sub": subject, "type": token_type, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _create_token(user_id, "access", timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(user_id: str) -> str:
    return _create_token(user_id, "refresh", timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))


# ---------------------------------------------------------------------------
# get_current_user — FastAPI dependency used by all protected routers (FR-022)
# ---------------------------------------------------------------------------

def get_current_user(
    token: Annotated[str, Depends(_oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> UserORM:
    """Decode the Bearer token and return the matching UserORM row.

    Raises HTTP 401 for any invalid, expired, or tampered token, and for
    tokens whose subject does not correspond to a user in the database.
    No stack trace is returned to the caller (NFR-023).
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise credentials_exc
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = db.query(UserORM).filter(UserORM.id == uuid.UUID(user_id)).first()
    if user is None:
        raise credentials_exc
    return user


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = {"str_strip_whitespace": True}


class RegisterResponse(BaseModel):
    id: str
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (FR-020)",
)
def register(body: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> RegisterResponse:
    """Create a new user account.

    Passwords are hashed with bcrypt (rounds=12) before storage.
    Returns HTTP 409 if the email is already registered.
    """
    hashed = hash_password(body.password)
    user = UserORM(
        id=uuid.uuid4(),
        email=body.email,
        hashed_pw=hashed,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    return RegisterResponse(id=str(user.id), email=user.email, created_at=user.created_at)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive JWT tokens (FR-021)",
)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate with email + password and return access + refresh tokens.

    Uses OAuth2PasswordRequestForm so the endpoint is compatible with the
    FastAPI /docs 'Authorize' button (username field carries the email).
    Returns HTTP 401 on any credential mismatch — no detail that leaks
    which field was wrong (NFR-023).
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user = db.query(UserORM).filter(UserORM.email == form.username).first()
    if user is None or not verify_password(form.password, user.hashed_pw):
        raise invalid_exc

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new token pair (FR-021)",
)
def refresh(body: RefreshRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    """Issue a new access + refresh token pair from a valid refresh token.

    Raises HTTP 401 if the token is expired, tampered, or not a refresh token.
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(body.refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise invalid_exc
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise invalid_exc
    except JWTError:
        raise invalid_exc

    user = db.query(UserORM).filter(UserORM.id == uuid.UUID(user_id)).first()
    if user is None:
        raise invalid_exc

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )
