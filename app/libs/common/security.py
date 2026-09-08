from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: tuple[str, ...]

    def has_role(self, role: str) -> bool:
        return role in self.roles


def issue_dev_token(subject: str, roles: list[str], ttl_seconds: int = 3600) -> str:
    """Local-development token minting. Production uses the corporate IdP."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": subject,
        "roles": roles,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not settings.auth_enabled:
        return Principal(subject="anonymous-dev", roles=(settings.admin_role,))
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = _decode(credentials.credentials, settings)
    subject = str(claims.get("sub") or "")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no subject")
    roles = claims.get("roles") or []
    if isinstance(roles, str):
        roles = [roles]
    return Principal(subject=subject, roles=tuple(str(r) for r in roles))


async def require_admin(
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not settings.auth_enabled:
        return principal
    if not principal.has_role(settings.admin_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return principal
