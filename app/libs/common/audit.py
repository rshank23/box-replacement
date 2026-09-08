from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditAction, AuditTrail

#: Values that must never be persisted into the audit details payload.
_SENSITIVE = {"password", "token", "secret", "session_id", "authorization"}


def _scrub(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    return {k: ("***REDACTED***" if k.lower() in _SENSITIVE else v) for k, v in details.items()}


async def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction | str,
    performed_by: str,
    source_system: str,
    details: dict[str, Any] | None = None,
    correlation_id: uuid.UUID | str | None = None,
) -> AuditTrail:
    """Append an immutable audit entry. Never raises on scrubbable content."""
    if isinstance(correlation_id, str):
        correlation_id = uuid.UUID(correlation_id)
    entry = AuditTrail(
        action=action.value if isinstance(action, AuditAction) else str(action),
        performed_by=performed_by,
        source_system=source_system,
        details=_scrub(details),
        correlation_id=correlation_id,
    )
    session.add(entry)
    await session.flush()
    return entry
