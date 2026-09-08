from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage

from .config import Settings, get_settings
from .logging_config import get_logger
from .metrics import BATCH_SUMMARY_SENT

log = get_logger(__name__)


@dataclass
class BatchSummary:
    """Counts and top failure reasons for one ingestion batch."""

    correlation_id: str
    started_at: datetime
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    total: int = 0
    success: int = 0
    duplicates: int = 0
    sam_pending: int = 0
    unclassified: int = 0
    failed: int = 0
    studies: list[str] = field(default_factory=list)
    top_reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def needs_attention(self) -> int:
        return self.failed + self.sam_pending

    def subject(self, prefix: str) -> str:
        state = "action required" if self.needs_attention else "complete"
        studies = ", ".join(self.studies[:3]) or "no study"
        if len(self.studies) > 3:
            studies += f" +{len(self.studies) - 3} more"
        return f"{prefix} Batch {state}: {self.success}/{self.total} archived ({studies})"

    def text_body(self) -> str:
        duration = max((self.finished_at - self.started_at).total_seconds(), 0)
        lines = [
            "MBox to Veeva Vault TMF - batch summary",
            "",
            f"Correlation id : {self.correlation_id}",
            f"Started        : {self.started_at.isoformat()}",
            f"Finished       : {self.finished_at.isoformat()} ({duration:.1f}s)",
            f"Studies        : {', '.join(self.studies) or '-'}",
            "",
            f"Files processed        : {self.total}",
            f"  Archived in VTMF     : {self.success}",
            f"  Duplicates skipped   : {self.duplicates}",
            f"  Filed as Unclassified: {self.unclassified}",
            f"  Awaiting SAM request : {self.sam_pending}",
            f"  Failed / exceptions  : {self.failed}",
        ]
        if self.top_reasons:
            lines += ["", "Top reasons:"]
            lines += [f"  {count:>4}  {reason}" for reason, count in self.top_reasons]
        if self.needs_attention:
            lines += [
                "",
                f"{self.needs_attention} item(s) need a human decision "
                "before the MBox retention window closes.",
            ]
        lines += ["", "This message contains no document content or regulated data."]
        return "\n".join(lines)


def _build_message(summary: BatchSummary, settings: Settings) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = summary.subject(settings.mail_subject_prefix)
    message["From"] = settings.mail_from
    message["To"] = ", ".join(settings.mail_recipients)
    message["X-Correlation-Id"] = summary.correlation_id
    message.set_content(summary.text_body())
    return message


def _send_sync(message: EmailMessage, settings: Settings) -> None:
    if settings.smtp_username and not settings.smtp_use_tls:
        raise ValueError("Refusing to send SMTP credentials over an unencrypted connection")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_batch_summary(summary: BatchSummary, settings: Settings | None = None) -> bool:
    """Send the batch digest. Never raises: a mail outage must not fail an ingestion run."""
    settings = settings or get_settings()
    if not settings.mail_enabled:
        log.info("batch_summary", delivery="disabled", **_log_fields(summary))
        return False
    if not settings.mail_recipients:
        log.warning("batch_summary_no_recipients", correlation_id=summary.correlation_id)
        return False

    try:
        await asyncio.to_thread(_send_sync, _build_message(summary, settings), settings)
    except Exception as exc:
        log.error("batch_summary_send_failed", error=str(exc), correlation_id=summary.correlation_id)
        return False

    BATCH_SUMMARY_SENT.inc()
    log.info("batch_summary", delivery="sent", recipients=len(settings.mail_recipients), **_log_fields(summary))
    return True


def _log_fields(summary: BatchSummary) -> dict:
    return {
        "correlation_id": summary.correlation_id,
        "total": summary.total,
        "success": summary.success,
        "duplicates": summary.duplicates,
        "unclassified": summary.unclassified,
        "sam_pending": summary.sam_pending,
        "failed": summary.failed,
        "top_reasons": summary.top_reasons,
    }
