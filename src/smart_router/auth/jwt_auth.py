"""JWT authentication for admin panel."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from ..core.config import get_settings

# JWT config
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_HOURS = 24


def _get_secret_key() -> str:
    """Get JWT secret key from env or config."""
    return os.environ.get("SMARTROUTER_JWT_SECRET", "smart-router-jwt-secret-key-change-in-production")


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _get_secret_key(), algorithm=_ALGORITHM)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a JWT token and return payload."""
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[_ALGORITHM])
        return payload
    except JWTError:
        return None


def verify_password(password: str) -> bool:
    """Verify admin password."""
    settings = get_settings()
    return password == settings.admin_password
