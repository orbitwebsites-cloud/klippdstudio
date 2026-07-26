"""Clerk session-token verification.

The app uses Clerk (https://clerk.com) for real user accounts. The frontend
attaches the Clerk session JWT as `Authorization: Bearer <token>`; here we verify
that token against Clerk's published JWKS and return the user id (the `sub`
claim). Verification is offline after the first JWKS fetch — no per-request call
to Clerk — and needs only the instance's issuer/JWKS URL, never a secret key.

When no Clerk issuer is configured the module reports `clerk_enabled() == False`
and the backend falls back to its anonymous per-browser isolation mode, so local
development and CI keep working without a Clerk instance.
"""
from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient


CLERK_ISSUER = os.environ.get("CLERK_ISSUER", "").strip().rstrip("/")
_jwks_override = os.environ.get("CLERK_JWKS_URL", "").strip()
CLERK_JWKS_URL = _jwks_override or (
    f"{CLERK_ISSUER}/.well-known/jwks.json" if CLERK_ISSUER else ""
)
# Optional allow-list of authorized parties (the `azp` claim), i.e. the origins
# permitted to use these tokens. Comma-separated; empty disables the check.
CLERK_AUTHORIZED_PARTIES = [
    p.strip() for p in os.environ.get("CLERK_AUTHORIZED_PARTIES", "").split(",") if p.strip()
]


class AuthError(Exception):
    """Raised when a session token is missing, malformed, or fails verification."""


def clerk_enabled() -> bool:
    return bool(CLERK_JWKS_URL)


_jwk_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # PyJWKClient caches fetched signing keys, so this is one network call
        # per key id for the lifetime of the process.
        _jwk_client = PyJWKClient(CLERK_JWKS_URL)
    return _jwk_client


def verify_session_token(token: str) -> str:
    """Verify a Clerk session JWT and return its subject (the Clerk user id).

    Raises AuthError on any problem so callers can translate it to a 401.
    """
    if not token:
        raise AuthError("missing session token")
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER or None,
            leeway=5,
            options={"require": ["exp", "sub"], "verify_iss": bool(CLERK_ISSUER)},
        )
    except Exception as exc:  # PyJWT and PyJWKClient raise several error types
        raise AuthError(str(exc)) from exc

    if CLERK_AUTHORIZED_PARTIES:
        azp = claims.get("azp")
        if azp and azp not in CLERK_AUTHORIZED_PARTIES:
            raise AuthError("authorized party not allowed")

    sub = claims.get("sub")
    if not sub:
        raise AuthError("token has no subject")
    return str(sub)
