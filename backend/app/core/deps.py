"""Request-scoped dependencies: database session and authenticated user.

Authentication has two modes, resolved per request in this order:

1. **Supabase (production).** When Supabase is configured, the Bearer token is
   verified as a Supabase-issued JWT and the local ``users`` row is lazily
   provisioned on first sight (``users.id`` = the token ``sub``).
2. **Legacy (dev/demo).** Only when Supabase is *not* configured *and*
   ``auth_legacy_login_enabled`` is set do we fall back to decoding the
   self-issued HS256 token minted by the legacy login endpoint.

If neither mode is available the request is rejected. Startup also refuses to
serve in that state (see ``app.main``), so this is defence in depth.

**Row-level security starts here.** The token is verified in ``get_session`` —
before the session is handed to anything — and the verified subject becomes the
session's identity: every transaction the session opens runs
``set_config('app.user_id', <sub>, true)`` first (see ``app.db.base``), which is
what the policies of migration 0003 key off. ``current_user`` then reads the
claims the session was opened with; it never verifies twice, and the user row
it loads (or provisions) is already governed by the policies — a user can read
and insert exactly their own row. A request with no token, or a token that does
not verify, gets an anonymous session: under the policies it sees nothing and
writes nothing, and ``current_user`` turns it into a 401.
"""

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import decode_access_token
from app.core.supabase_auth import AuthError, verify_supabase_jwt
from app.db.base import SessionLocal, bind_session_identity
from app.db.models import User
from app.services.organisations import ensure_organisation

bearer = HTTPBearer(auto_error=False)

_CLAIMS_KEY = "auth_claims"
_MODE_KEY = "auth_mode"

_UNAUTHORISED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)
_INACTIVE = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")


def _verified_identity(token: str) -> tuple[str, uuid.UUID, dict[str, Any]] | None:
    """Verify a bearer token and name its subject, without touching the database.

    Returns ``(mode, user_id, claims)`` or None for a token that does not
    verify. Verification failure here is deliberately not an HTTP error: the
    session simply stays anonymous, the policies show it nothing, and
    ``current_user`` is where "anonymous" becomes 401 — so the unauthenticated
    endpoints (register, login) keep working under a stale Authorization header.
    """
    if settings.supabase_configured:
        try:
            claims = verify_supabase_jwt(token)
        except AuthError:
            return None
        mode = "supabase"
    elif settings.auth_legacy_login_enabled:
        claims = decode_access_token(token) or {}
        mode = "legacy"
    else:
        return None

    try:
        return mode, uuid.UUID(str(claims["sub"])), claims
    except (KeyError, ValueError):
        return None


async def get_session(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
):
    """The request's database session, identity-bound when the token verifies."""
    info: dict[str, Any] = {}
    if credentials is not None:
        identity = _verified_identity(credentials.credentials)
        if identity is not None:
            mode, user_id, claims = identity
            info = {"rls_user_id": str(user_id), _MODE_KEY: mode, _CLAIMS_KEY: claims}

    async with SessionLocal(info=info) as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def _full_name_from_claims(claims: dict[str, Any]) -> str:
    metadata = claims.get("user_metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("full_name") or metadata.get("name") or "")


def _organisation_from_claims(claims: dict[str, Any]) -> str:
    metadata = claims.get("user_metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("organisation") or metadata.get("org") or "")


async def _provision_from_claims(db: AsyncSession, claims: dict[str, Any]) -> User:
    """Load — or, on first sight, create — the local user for a verified token.

    The local row mirrors the Supabase identity: ``id`` is the token ``sub``, and
    email/full_name/organisation come from the token (the frontend sends full_name
    and organisation as Supabase user_metadata at sign-up). ``hashed_password`` is
    empty because the backend never holds credentials for Supabase-managed accounts.

    Under row-level security both halves are the narrowest possible operations:
    the SELECT can only ever see the caller's own row, and the INSERT policy
    admits exactly the row whose id is the verified subject.
    """
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError):
        raise _UNAUTHORISED from None

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # users.org_id is NOT NULL (E2's tenancy): a freshly provisioned account
        # joins the catch-all organisation here — tenant assignment is a deliberate
        # act owned by E2. The `organisation` display string is filled from the
        # token's user_metadata, which the frontend now sends at sign-up. (Follow-up
        # for E2: org_id could be routed through ensure_organisation() on that same
        # claim, mirroring the legacy register flow — flagged in the PR.)
        org = await ensure_organisation(db, "")
        user = User(
            id=user_id,
            email=(claims.get("email") or "").lower(),
            full_name=_full_name_from_claims(claims),
            org_id=org.id,
            organisation=_organisation_from_claims(claims),
            hashed_password="",  # Supabase owns the credential; we store none.
            is_active=True,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            # A concurrent request provisioned the same subject first; adopt it.
            await db.rollback()
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is None:
                raise _UNAUTHORISED from None
        else:
            await db.refresh(user)
            from app.services.audit import record_audit

            await record_audit(
                db,
                org_id=user.org_id,
                actor_id=user.id,
                action="auth.provision",
                subject_type="user",
                subject_id=user.id,
                payload={"email": user.email, "mode": "supabase"},
            )
            await db.commit()

    if not user.is_active:
        raise _INACTIVE
    return user


async def _legacy_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Resolve a user from an already-verified legacy token subject."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise _UNAUTHORISED
    if not user.is_active:
        raise _INACTIVE
    return user


async def current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None:
        raise _UNAUTHORISED

    # Normally the session dependency already verified the token when it bound
    # the session's identity. A session that arrived without one (a test
    # harness overriding get_session, or any future caller wiring a session by
    # hand) gets the same verification here, and the identity is bound to it —
    # authentication must not silently weaken because the session came from
    # somewhere else.
    mode = db.info.get(_MODE_KEY)
    claims = db.info.get(_CLAIMS_KEY)
    if mode is None or claims is None:
        identity = _verified_identity(credentials.credentials)
        if identity is None:
            raise _UNAUTHORISED
        mode, user_id, claims = identity
        db.info[_MODE_KEY] = mode
        db.info[_CLAIMS_KEY] = claims
        await bind_session_identity(db, user_id)

    if mode == "supabase":
        return await _provision_from_claims(db, claims)
    return await _legacy_user(db, uuid.UUID(str(claims["sub"])))


CurrentUser = Annotated[User, Depends(current_user)]
