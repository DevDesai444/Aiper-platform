"""Auth behaviour: Supabase verification + lazy provisioning, and the dev-gated
legacy login (flag gating + rate limiting), plus the fail-closed startup guard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import func, select

from app.db.models import User
from app.services.organisations import ensure_organisation
from tests.conftest import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    TEST_SUPABASE_SECRET,
    TEST_SUPABASE_URL,
    mint_alg_none,
    mint_hs256,
)

ME = "/api/v1/auth/me"
LOGIN = "/api/v1/auth/login"
REGISTER = "/api/v1/auth/register"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _count_users(session_factory) -> int:
    async with session_factory() as s:
        return (await s.execute(select(func.count()).select_from(User))).scalar_one()


# ──────────────────────────── Supabase verification ────────────────────────────


@pytest.mark.asyncio
async def test_valid_token_provisions_user_once(client, session_factory, supabase_configured):
    sub = uuid.uuid4()
    token = mint_hs256(sub=sub, email="Alice@Example.com", full_name="Alice Doe")

    r1 = await client.get(ME, headers=_bearer(token))
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["id"] == str(sub)
    assert body["email"] == "alice@example.com"  # normalised to lowercase
    assert body["full_name"] == "Alice Doe"
    assert await _count_users(session_factory) == 1

    # Second call with the same token loads the existing row — no duplicate insert.
    r2 = await client.get(ME, headers=_bearer(token))
    assert r2.status_code == 200
    assert r2.json()["id"] == str(sub)
    assert await _count_users(session_factory) == 1


@pytest.mark.asyncio
async def test_full_name_falls_back_to_metadata_name(client, supabase_configured):
    sub = uuid.uuid4()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(sub),
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "email": "bob@example.com",
            "user_metadata": {"name": "Bob Jones"},  # `name`, not `full_name`
        },
        TEST_SUPABASE_SECRET,
        algorithm="HS256",
    )
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Bob Jones"


@pytest.mark.asyncio
async def test_wrong_audience_is_401(client, supabase_configured):
    token = mint_hs256(sub=uuid.uuid4(), audience="anon")
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_is_401(client, supabase_configured):
    token = mint_hs256(sub=uuid.uuid4(), issuer="https://evil.example.com/auth/v1")
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_is_401(client, supabase_configured):
    token = mint_hs256(sub=uuid.uuid4(), expires_in=-30)
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_signature_is_401(client, supabase_configured):
    token = mint_hs256(sub=uuid.uuid4(), secret="a-different-secret-that-is-at-least-32-bytes")
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_missing_sub_is_401(client, supabase_configured):
    token = mint_hs256(sub=None)  # no subject claim
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_alg_none_is_401(client, supabase_configured):
    r = await client.get(ME, headers=_bearer(mint_alg_none()))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_garbage_token_is_401(client, supabase_configured):
    r = await client.get(ME, headers=_bearer("this.is.not-a-jwt"))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_missing_authorization_header_is_401(client, supabase_configured):
    r = await client.get(ME)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_inactive_user_is_403(client, session_factory, supabase_configured):
    sub = uuid.uuid4()
    async with session_factory() as s:
        # users.org_id is NOT NULL since the tenancy migration: an account has
        # to belong to an organisation, so place this one in the catch-all.
        org = await ensure_organisation(s, "")
        s.add(
            User(
                id=sub,
                email="inactive@example.com",
                full_name="Inactive",
                org_id=org.id,
                organisation="",
                hashed_password="",
                is_active=False,
            )
        )
        await s.commit()

    token = mint_hs256(sub=sub, email="inactive@example.com")
    r = await client.get(ME, headers=_bearer(token))
    assert r.status_code == 403


# ───────────────────────────────── legacy login ────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_endpoints_404_when_disabled(client):
    # reset_auth_settings leaves every mode off by default.
    r_login = await client.post(LOGIN, json={"email": "a@b.com", "password": "whatever"})
    assert r_login.status_code == 404
    r_reg = await client.post(
        REGISTER,
        json={"email": "a@b.com", "password": "password123", "full_name": "A", "organisation": "O"},
    )
    assert r_reg.status_code == 404


@pytest.mark.asyncio
async def test_legacy_flow_when_enabled(client, legacy_configured):
    reg = await client.post(
        REGISTER,
        json={
            "email": "dev@example.com",
            "password": "password123",
            "full_name": "Dev User",
            "organisation": "Acme",
        },
    )
    assert reg.status_code == 201, reg.text
    reg_token = reg.json()["access_token"]

    me = await client.get(ME, headers=_bearer(reg_token))
    assert me.status_code == 200
    assert me.json()["email"] == "dev@example.com"

    login = await client.post(LOGIN, json={"email": "dev@example.com", "password": "password123"})
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "dev@example.com"


@pytest.mark.asyncio
async def test_legacy_token_rejected_once_supabase_is_active(client, legacy_configured):
    # Mint a legacy self-issued token while the legacy path is enabled.
    reg = await client.post(
        REGISTER,
        json={
            "email": "switch@example.com",
            "password": "password123",
            "full_name": "Switch",
            "organisation": "Acme",
        },
    )
    assert reg.status_code == 201
    legacy_token = reg.json()["access_token"]

    # Flip to production (Supabase) mode: the legacy token must no longer authenticate.
    from app.config import settings

    settings.supabase_url = TEST_SUPABASE_URL
    settings.supabase_jwt_secret = TEST_SUPABASE_SECRET
    settings.auth_legacy_login_enabled = False

    r = await client.get(ME, headers=_bearer(legacy_token))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_is_rate_limited(client, legacy_configured):
    await client.post(
        REGISTER,
        json={
            "email": "rl@example.com",
            "password": "password123",
            "full_name": "Rate",
            "organisation": "Acme",
        },
    )
    # 10 attempts are allowed (wrong password → 401); the 11th is throttled (429).
    for attempt in range(10):
        r = await client.post(LOGIN, json={"email": "rl@example.com", "password": "wrong-pass"})
        assert r.status_code == 401, f"attempt {attempt} unexpectedly {r.status_code}"

    throttled = await client.post(LOGIN, json={"email": "rl@example.com", "password": "wrong-pass"})
    assert throttled.status_code == 429


# ──────────────────────────── fail-closed startup guard ─────────────────────────


def test_startup_refuses_without_any_auth_mode():
    from app.core.startup import ensure_auth_configured

    with pytest.raises(RuntimeError):
        ensure_auth_configured()  # baseline: nothing configured


def test_startup_ok_with_legacy_flag(legacy_configured):
    from app.core.startup import ensure_auth_configured

    ensure_auth_configured()  # must not raise


def test_startup_ok_with_supabase(supabase_configured):
    from app.core.startup import ensure_auth_configured

    ensure_auth_configured()  # must not raise


# ─────────────────────────── verifier unit-level checks ─────────────────────────


def test_verifier_accepts_valid_hs256(supabase_configured):
    from app.core.supabase_auth import verify_supabase_jwt

    sub = uuid.uuid4()
    claims = verify_supabase_jwt(mint_hs256(sub=sub, email="v@example.com"))
    assert claims["sub"] == str(sub)
    assert claims["email"] == "v@example.com"


def test_verifier_rejects_hs256_when_no_secret_configured():
    from app.config import settings
    from app.core.supabase_auth import AuthError, verify_supabase_jwt

    settings.supabase_url = TEST_SUPABASE_URL  # issuer resolvable...
    settings.supabase_jwt_secret = ""  # ...but no HS256 secret to verify with

    with pytest.raises(AuthError):
        verify_supabase_jwt(mint_hs256(sub=uuid.uuid4()))
