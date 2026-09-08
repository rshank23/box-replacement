from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

TRANSFERS_SUCCESS = Counter(
    "transfers_success_total", "Documents successfully created in Veeva Vault TMF", ["study"]
)
TRANSFERS_FAILED = Counter("transfers_failed_total", "Transfers that ended in FAILED or EXCEPTION", ["study"])
TRANSFERS_DUPLICATE = Counter("transfers_duplicate_skipped_total", "Transfers skipped as duplicates")
FAILURES_BY_REASON = Counter("failures_by_reason_total", "Failure queue entries by reason", ["reason"])
SAM_REQUESTS = Counter("sam_requests_total", "Items routed to the SAM action queue")
UNCLASSIFIED_UPLOADS = Counter(
    "unclassified_uploads_total", "Unmapped documents filed in VTMF under the Unclassified fallback"
)
BATCH_SUMMARY_SENT = Counter("batch_summary_emails_total", "Batch digest emails delivered")
PICKLIST_REFRESHES = Counter("picklist_refresh_total", "VTMF picklist cache refreshes", ["trigger"])

FAILURE_QUEUE_DEPTH = Gauge("failure_queue_depth", "Open items in the failure queue")
SAM_QUEUE_DEPTH = Gauge("sam_queue_depth", "Open items in the SAM action queue")
MAPPING_HIT_RATE = Gauge("mapping_hit_rate", "Share of processed files that resolved to a mapping rule")
FOLDER_AGE_DAYS = Gauge("folder_age_days", "Age in days of the oldest unprocessed MBox study folder", ["study"])
WATCHER_LAST_RUN = Gauge("watcher_last_run_timestamp", "Unix timestamp of the last completed watcher scan")
VAULT_CIRCUIT_OPEN = Gauge("vault_circuit_breaker_open", "1 when the Vault circuit breaker is open")

VAULT_API_LATENCY = Histogram(
    "vault_api_latency_seconds",
    "Latency of Veeva Vault API calls",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
PIPELINE_LATENCY = Histogram(
    "pipeline_latency_seconds",
    "End-to-end orchestration latency per document",
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 1800, 3600),
)


def record_failure(reason: str, study: str | None = None) -> None:
    FAILURES_BY_REASON.labels(reason=reason).inc()
    TRANSFERS_FAILED.labels(study=study or "unknown").inc()


def record_success(study: str | None = None) -> None:
    TRANSFERS_SUCCESS.labels(study=study or "unknown").inc()
