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
from app.db.base import get_session
from app.db.models import User
from app.services.organisations import ensure_organisation

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]

_UNAUTHORISED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)
_INACTIVE = HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")


def _full_name_from_claims(claims: dict[str, Any]) -> str:
    metadata = claims.get("user_metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("full_name") or metadata.get("name") or "")


async def _provision_from_claims(db: AsyncSession, claims: dict[str, Any]) -> User:
    """Load — or, on first sight, create — the local user for a verified token.

    The local row mirrors the Supabase identity: ``id`` is the token ``sub``,
    email/full_name come from the token, and ``hashed_password`` is empty because
    the backend never holds credentials for Supabase-managed accounts.
    """
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError):
        raise _UNAUTHORISED from None

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # users.org_id is NOT NULL: an account has to land in a tenant. A
        # Supabase token carries no organisation claim, so a freshly provisioned
        # account joins the catch-all organisation, exactly as an account with a
        # blank organisation string does. Moving somebody to the right tenant is
        # a deliberate act, not something to guess from a token.
        org = await ensure_organisation(db, "")
        user = User(
            id=user_id,
            email=(claims.get("email") or "").lower(),
            full_name=_full_name_from_claims(claims),
            org_id=org.id,
            organisation="",
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

    if not user.is_active:
        raise _INACTIVE
    return user


async def _legacy_user(db: AsyncSession, token: str) -> User:
    """Resolve a user from a self-issued (legacy) HS256 token."""
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise _UNAUTHORISED
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError:
        raise _UNAUTHORISED from None

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
    token = credentials.credentials

    if settings.supabase_configured:
        try:
            claims = verify_supabase_jwt(token)
        except AuthError:
            raise _UNAUTHORISED from None
        return await _provision_from_claims(db, claims)

    if settings.auth_legacy_login_enabled:
        return await _legacy_user(db, token)

    # No auth mode configured — fail closed (startup should already have refused).
    raise _UNAUTHORISED


CurrentUser = Annotated[User, Depends(current_user)]
