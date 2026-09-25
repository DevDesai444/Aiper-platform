"""Supabase (GoTrue) JWT verification — the production identity path.

A client presents a Supabase-issued access token as a Bearer credential. We
verify its signature and claims here; the backend never issues its own tokens
in production and never sees the user's password.

Two verification modes, chosen by the token's ``alg`` header:

* **RS256 / ES256** — asymmetric. The signing key is fetched from the project
  JWKS endpoint (``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``) and cached by
  :class:`jwt.PyJWKClient`, so key material is fetched once and reused.
* **HS256** — symmetric, using ``settings.supabase_jwt_secret``. This is what
  makes the test suite hermetic: tests mint their own HS256 tokens with a test
  secret and never touch the network.

Every token must carry a valid signature plus the required claims: issuer
``{SUPABASE_URL}/auth/v1``, audience ``authenticated``, and a non-expired
``exp``. Any failure raises a single :class:`AuthError`; callers map that to a
401 without leaking which check failed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import settings

# Asymmetric algorithms Supabase uses for JWKS-signed tokens.
_ASYMMETRIC_ALGORITHMS = ("RS256", "ES256")
_AUDIENCE = "authenticated"
# Required claims must be *present*, not merely valid-if-present.
_REQUIRED_CLAIMS = ("exp", "iss", "aud", "sub")


class AuthError(Exception):
    """Raised for any token that fails Supabase verification.

    Deliberately opaque to the caller: the message aids logging/tests but the
    HTTP layer collapses every instance to a generic 401.
    """


@lru_cache(maxsize=1)
def _jwks_client(url: str) -> PyJWKClient:
    """Cached PyJWKClient. Keyed on the URL so a config change yields a new client.

    ``PyJWKClient`` keeps its own in-memory cache of fetched signing keys, so a
    single long-lived instance avoids refetching JWKS on every request.
    """
    return PyJWKClient(url, cache_keys=True)


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return its decoded claims.

    Raises :class:`AuthError` on any problem: malformed token, unsupported
    algorithm, bad signature, wrong issuer/audience, expiry, missing claims, or
    an unreachable JWKS endpoint.
    """
    if not token:
        raise AuthError("Missing token")

    issuer = settings.supabase_issuer
    if not issuer:
        # Supabase verification was requested but no URL is configured to pin the
        # issuer against — refuse rather than accept an unpinned token.
        raise AuthError("Supabase URL is not configured; cannot verify issuer")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("Malformed token header") from exc

    alg = header.get("alg")
    decode_options = {"require": list(_REQUIRED_CLAIMS)}

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("HS256 token received but no Supabase JWT secret is configured")
            key: Any = settings.supabase_jwt_secret
            algorithms = ["HS256"]
        elif alg in _ASYMMETRIC_ALGORITHMS:
            jwks_url = settings.supabase_jwks_url
            if not jwks_url:
                raise AuthError("Asymmetric token received but no JWKS URL is configured")
            try:
                key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            except jwt.PyJWTError as exc:
                raise AuthError("Unable to resolve signing key") from exc
            except Exception as exc:  # noqa: BLE001 - network/JWKS fetch failures
                raise AuthError("Unable to fetch JWKS signing keys") from exc
            algorithms = list(_ASYMMETRIC_ALGORITHMS)
        else:
            raise AuthError(f"Unsupported token algorithm: {alg!r}")

        payload: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=_AUDIENCE,
            issuer=issuer,
            options=decode_options,
        )
    except AuthError:
        raise
    except jwt.PyJWTError as exc:
        # Signature, audience, issuer, expiry and missing-claim failures land here.
        raise AuthError(str(exc) or "Token verification failed") from exc

    if not payload.get("sub"):
        raise AuthError("Token missing subject (sub) claim")

    return payload
