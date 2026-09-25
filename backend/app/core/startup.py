"""Startup guards that run before the application begins serving."""

from app.config import settings


def ensure_auth_configured() -> None:
    """Fail closed unless at least one authentication mode is configured.

    Called from the app lifespan so a misconfigured deployment refuses to serve
    rather than silently accepting no one (or, worse, everyone).
    """
    if not settings.auth_configured:
        raise RuntimeError(
            "Refusing to start: no authentication mode is configured. "
            "Set SUPABASE_URL (with SUPABASE_JWT_SECRET or a reachable JWKS) for the "
            "production Supabase path, or AUTH_LEGACY_LOGIN_ENABLED=1 for the legacy "
            "dev/demo login."
        )
