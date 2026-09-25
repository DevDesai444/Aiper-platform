"""Registration, login and the current account.

Registration and login are the **legacy** self-issued auth path. They are only
available when ``auth_legacy_login_enabled`` is set (dev/demo); in production the
frontend authenticates against Supabase and these endpoints return 404. Login is
additionally rate-limited to blunt brute-force attempts. ``/auth/me`` works in
both modes — it resolves whoever ``current_user`` authenticated.

These two endpoints are the only place the API touches the database with no
authenticated identity, so under row-level security (migration 0003) they work
through the narrow carve-outs built for them and nothing else:

* Registration mints the account id first and **binds it as the session
  identity before inserting** — the users INSERT policy admits exactly the row
  whose id is the bound identity. A duplicate email surfaces as the unique
  constraint, not as a pre-query (an anonymous session cannot see other
  accounts to pre-check against).
* The organisation upsert and the pending-share claim run through SECURITY
  DEFINER functions: both act on rows the not-yet-existing user could never
  see.
* Login looks the account up by email through ``aiper_auth_user_by_email`` —
  one exact-match row, dev-gated endpoint — and binds the identity only after
  the password verifies.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import schemas
from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import bind_session_identity
from app.db.models import User
from app.services import audit
from app.services.organisations import ensure_organisation

router = APIRouter(prefix="/auth", tags=["auth"])

# Bound brute-force attempts on the legacy login: 10 tries per 5 minutes per
# (email, client IP). In-process only — sufficient for the dev/demo posture.
login_rate_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=300)

_CLAIM_SHARES = text("SELECT aiper_claim_pending_shares(CAST(:user_id AS uuid), :email)")
_USER_BY_EMAIL = text("SELECT * FROM aiper_auth_user_by_email(:email)")


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

    # The account id is chosen here so it can be the session identity before
    # the row exists; from this point the session acts as the new user.
    user_id = uuid.uuid4()
    await bind_session_identity(db, user_id)

    # The free-text organisation name decides the tenant: the same name
    # joins the same organisation, using the slug rule migration 0002 applied
    # to existing accounts.
    org = await ensure_organisation(db, payload.organisation)
    user = User(
        id=user_id,
        email=email,
        org_id=org.id,
        full_name=payload.full_name.strip(),
        organisation=payload.organisation.strip(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with that email already exists"
        ) from None

    # Claim any share addressed to this email before the account existed.
    await db.execute(_CLAIM_SHARES, {"user_id": str(user.id), "email": email})
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.AUTH_REGISTER,
        subject_type="user",
        subject_id=user.id,
        payload={"email": email},
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

    user = (
        (await db.execute(select(User).from_statement(_USER_BY_EMAIL), {"email": email}))
        .scalars()
        .one_or_none()
    )
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    # Authenticated: the rest of the transaction happens as this user.
    await bind_session_identity(db, user.id)
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.AUTH_LOGIN,
        subject_type="user",
        subject_id=user.id,
        payload={"email": email},
    )
    await db.commit()
    return _token(user)


@router.get("/me", response_model=schemas.UserOut)
async def me(user: CurrentUser) -> User:
    return user
