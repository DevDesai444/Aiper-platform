"""Registration, login and the current account.

Registration and login are the **legacy** self-issued auth path. They are only
available when ``auth_legacy_login_enabled`` is set (dev/demo); in production the
frontend authenticates against Supabase and these endpoints return 404. Login is
additionally rate-limited to blunt brute-force attempts. ``/auth/me`` works in
both modes — it resolves whoever ``current_user`` authenticated.
"""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from app import schemas
from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import DocumentCollaborator, User
from app.services.organisations import ensure_organisation

router = APIRouter(prefix="/auth", tags=["auth"])

# Bound brute-force attempts on the legacy login: 10 tries per 5 minutes per
# (email, client IP). In-process only — sufficient for the dev/demo posture.
login_rate_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=300)


def _require_legacy_enabled() -> None:
    """Hide the self-issued auth endpoints unless the legacy path is enabled."""
    if not settings.auth_legacy_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _token(user: User) -> schemas.TokenOut:
    return schemas.TokenOut(
        access_token=create_access_token(str(user.id), {"email": user.email}),
        user=schemas.UserOut.model_validate(user),
    )


@router.post("/register", response_model=schemas.TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: schemas.RegisterRequest, db: DbSession) -> schemas.TokenOut:
    _require_legacy_enabled()
    email = payload.email.lower()
    exists = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")

    # The free-text organisation name decides the tenant: the same name
    # joins the same organisation, using the slug rule migration 0002 applied
    # to existing accounts.
    org = await ensure_organisation(db, payload.organisation)
    user = User(
        email=email,
        org_id=org.id,
        full_name=payload.full_name.strip(),
        organisation=payload.organisation.strip(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()

    # Claim any share addressed to this email before the account existed.
    await db.execute(
        update(DocumentCollaborator)
        .where(DocumentCollaborator.email == email, DocumentCollaborator.user_id.is_(None))
        .values(user_id=user.id, invite_status="accepted")
    )
    await db.commit()
    await db.refresh(user)
    return _token(user)


@router.post("/login", response_model=schemas.TokenOut)
async def login(payload: schemas.LoginRequest, db: DbSession, request: Request) -> schemas.TokenOut:
    _require_legacy_enabled()

    email = payload.email.lower()
    client_ip = request.client.host if request.client else "unknown"
    if not login_rate_limiter.hit(f"{email}|{client_ip}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Please try again later.",
        )

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return _token(user)


@router.get("/me", response_model=schemas.UserOut)
async def me(user: CurrentUser) -> User:
    return user
