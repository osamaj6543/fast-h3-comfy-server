"""API-key authentication and HMAC-signed result URLs."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.config import Settings


def authenticate(settings: Settings, x_api_key: Optional[str]) -> Optional[str]:
    """Return the key identity for a valid key, None when unauthenticated.

    When no API keys are configured (local dev), requests run as 'anonymous'.
    """
    if not settings.auth_enabled:
        return "anonymous"
    if x_api_key and any(
        hmac.compare_digest(x_api_key, k) for k in settings.api_key_set
    ):
        return x_api_key
    return None


async def require_api_key(request: Request, x_api_key: Optional[str] = Header(None)) -> Optional[str]:
    """Optional-key dependency: returns the key identity, or None when the
    request is unauthenticated. Endpoints decide whether None is allowed
    (the content endpoint accepts signed tokens instead)."""
    settings = request.app.state.settings
    return authenticate(settings, x_api_key)


def ensure_key(api_key: Optional[str]) -> str:
    """Raise 401 if the request is unauthenticated."""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )
    return api_key


# -- signed result URLs ------------------------------------------------------

def sign_token(secret: bytes, job_id: str, expires_at: int) -> str:
    msg = f"{job_id}.{expires_at}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def make_signed_token(settings: Settings, job_id: str, ttl_seconds: int) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    return sign_token(settings.signing_secret, job_id, expires_at), expires_at


def verify_signed_token(settings: Settings, job_id: str, expires_at: int, token: str) -> bool:
    if time.time() > expires_at:
        return False
    expected = sign_token(settings.signing_secret, job_id, expires_at)
    return hmac.compare_digest(token, expected)
