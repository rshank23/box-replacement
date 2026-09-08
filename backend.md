# Backend (Python + FastAPI): MBox → Veeva Vault TMF Automation

Automates post-study archival of clinical trial documents from MBox into Veeva Vault TMF (VTMF) with
mapping, validation, exception handling and full auditability. Designed for GxP and 21 CFR Part 11.

Ingestion is **batch and post-study**, not continuous. MBox study folders are retained for roughly
90 days, so folder age is tracked and alerted at 60 and 75 days.

---

## 1. Architecture

| Component | Path | Responsibility |
| --- | --- | --- |
| Watcher | `app/workers/watcher` | APScheduler job that polls MBox, expands ZIPs, hashes files, de-duplicates and submits batches |
| Integration API | `app/services/integration_api` | FastAPI app: orchestration (map → validate → upload) plus the REST surface used by the UI and operations |
| Domain layer | `app/services/integration_api/domain` | `MappingEngine`, `ValidationService`, `Orchestrator` |
| Shared libraries | `app/libs/common` | Config, DB, ORM models, audit, metrics, security, path parsing, archive handling, Vault clients |
| Frontend | `frontend` | React + TypeScript operations UI (see `frontend/README.md`) |
| Database | `db/migrations` | PostgreSQL schema and Part 11 triggers |
| Deployment | `deploy` | Dockerfile, docker-compose, Prometheus scrape config and alert rules |
```
MBox (read-only)
   │  scan every MBOX_POLL_ISO_DURATION
   ▼
Watcher ──► POST /api/transfers/submit ──► Orchestrator
                                             │
                     ┌───────────────────────┼────────────────────────┐
                     ▼                       ▼                        ▼
              failure_queue           sam_action_queue          Veeva Vault TMF
             (EXCEPTION)               (SAM_PENDING)                (SUCCESS)
                     └───────────────────────┴────────────────────────┘
                                             ▼
                         transfer_log  +  audit_trail (append-only)
                                             ▼
                                    batch summary digest
```

### End-to-end sequence

```mermaid
sequenceDiagram
    autonumber
    participant CRO as CRO
    participant MBOX as MBox
    participant WATCHER as Watcher (APScheduler)
    participant UTIL as Utility (FastAPI)
    participant VTMF as VTMF (Vault APIs)
    participant MAIL as Mail (Digest)

    CRO->>MBOX: Upload ZIP + metadata (post-study)
    WATCHER-->>MBOX: Poll / detect new study drop
    WATCHER->>WATCHER: Extract ZIPs (ZIP_MAX_DEPTH), compute SHA-256
    WATCHER->>WATCHER: Deduplicate on source_path + checksum
    WATCHER->>UTIL: Submit file list (correlation_id, checksums)

    UTIL->>VTMF: Authenticate (once per batch, session reused)
    UTIL->>VTMF: Prefetch picklists (types, subtypes, classifications) [TTL cache]

    loop per file
        UTIL->>UTIL: Parse path -> Study/Country/Site/Category
        UTIL->>UTIL: Mapping Engine (exact -> regex -> study default -> global)
        alt Mapping found
            UTIL->>UTIL: Validate required fields and picklists
            alt Picklist value unknown to VTMF
                UTIL->>UTIL: Enqueue SAM action (no upload)
            else Valid metadata
                alt File > LARGE_FILE_MB
                    UTIL->>VTMF: Stage file
                end
                UTIL->>VTMF: Create document (external_id = idempotency key)
                VTMF-->>UTIL: vault_document_id
                UTIL->>VTMF: Fetch document info
                UTIL->>UTIL: Verify size and checksum
            end
        else Mapping missing
            opt UNMAPPED_POLICY = unclassified
                UTIL->>VTMF: Create Unclassified document
                VTMF-->>UTIL: vault_document_id
            end
            UTIL->>UTIL: Enqueue failure queue (NO_MAPPING)
        end
        UTIL->>UTIL: Write transfer_log + audit_trail, emit metrics
        note right of UTIL: Retry/backoff on 429 and 5xx,<br/>rate limiter, circuit breaker
    end

    UTIL->>MAIL: Batch summary (success / failed / SAM / unclassified + top reasons)
```

---

## 2. Prerequisites

- Python 3.11+, Docker and Docker Compose
- PostgreSQL 14+
- A least-privilege service account with **read-only** access to the MBox share
- Vault integration user credentials supplied through the environment or a secret store

---

## 3. Configuration

All settings are environment variables (see `.env.example`); `.env` is loaded for local development
only and must never be committed.

| Variable | Default | Notes |
| --- | --- | --- |
| `DB_DSN` | `postgresql+psycopg2://app:app@localhost:5432/mbox_vtmf` | Async driver is derived automatically; Alembic uses the sync form |
| `MBOX_ROOT` | `./demo/mbox` | Root of the MBox tree |
| `MBOX_POLL_ISO_DURATION` | `PT30M` | ISO-8601 scan interval |
| `MIN_FOLDER_DEPTH` | `3` | Study/Country/Site must be present |
| `ALLOWED_EXTENSIONS` | `.pdf,.docx,.xlsx,.zip` | Everything else is skipped and logged |
| `MAX_FILE_MB` | `500` | Per-file ceiling, also enforced on archive members |
| `ZIP_MAX_DEPTH` | `-1` | `-1` extracts nested archives at every level |
| `FOLDER_AGE_WARN_DAYS` / `FOLDER_AGE_CRITICAL_DAYS` / `FOLDER_RETENTION_DAYS` | `60` / `75` / `90` | Retention alerting |
| `VAULT_CLIENT` | `fake` | `fake` uses the in-memory stub, `real` calls Vault |
| `VAULT_BASE_URL`, `VAULT_API_VERSION` | –, `v26.2` | Vault connection |
| `VAULT_AUTH_MODE` | `password` | `token` uses an API access token (Veeva's recommendation) |
| `VAULT_API_TOKEN` / `VAULT_USERNAME` + `VAULT_PASSWORD` | – | Credentials for the chosen mode |
| `VAULT_EXPECTED_VAULT_ID` | unset | Refuses to proceed if Vault authentication defaults to another Vault |
| `VAULT_MIGRATION_MODE` / `VAULT_NO_TRIGGERS` | `false` | Bypass entry actions and notifications during bulk archival |
| `VAULT_FILE_PART_MB` / `VAULT_JOB_TIMEOUT_S` | `25` / `1800` | Resumable upload part size (clamped 5–52 MB) and commit-job timeout |
| `LARGE_FILE_MB` | `50` | Above this size the staged-upload flow is used |
| `RATE_LIMIT_PER_SEC` | `5` | Client-side limiter |
| `RETRY_MAX_ATTEMPTS` / `RETRY_BACKOFF_S` | `3` / `30,120,600` | Retry policy for 429 and 5xx |
| `CIRCUIT_BREAKER_FAIL_MAX` / `CIRCUIT_BREAKER_RESET_S` | `5` / `60` | Circuit breaker |
| `PICKLIST_CACHE_TTL_S` | `3600` | Picklists are prefetched once per batch and reused until this expires |
| `UNMAPPED_POLICY` | `queue` | `queue` holds unmapped files for a human; `unclassified` also files them in VTMF |
| `UNCLASSIFIED_DOCUMENT_TYPE` / `_SUBTYPE` / `UNCLASSIFIED_CLASSIFICATION` | `Unclassified` | Metadata used by the fallback |
| `MAIL_ENABLED`, `SMTP_*`, `MAIL_FROM`, `MAIL_TO`, `MAIL_SUBJECT_PREFIX` | off | Batch summary digest |
| `INTEGRATION_API_BASE_URL` | `http://localhost:8080` | Used by the watcher |
| `SERVICE_ACCOUNT_ID` | `svc-mbox` | Recorded as `performed_by` for system actions |
| `AUTH_ENABLED`, `ADMIN_ROLE`, `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_AUDIENCE` | `true`, `tmf_admin`, … | RBAC |
| `DEV_TOKEN_ENABLED` | `false` | Exposes `POST /api/admin/dev-token`; local development only |
| `CORS_ALLOW_ORIGINS` | empty | CORS is disabled unless origins are listed |
| `LOG_LEVEL`, `PROMETHEUS_ENABLED` | `INFO`, `true` | Observability |

---

## 4. Run locally

### Docker Compose (recommended)

```bash
# Builds the images, starts PostgreSQL, applies migrations, generates demo data and seeds rules
docker compose -f deploy/docker-compose.yml up -d --build

curl http://localhost:8080/health
curl http://localhost:8080/api/dashboard/stats

# UI at http://localhost:3000 (nginx reverse-proxies /api to the Integration API)

# Optional Prometheus
docker compose -f deploy/docker-compose.yml --profile observability up -d
```

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

alembic -c db/alembic.ini upgrade head
python scripts/make_demo_data.py                 # nested + corrupt ZIP fixtures
python scripts/seed.py                           # mapping rules + picklist cache

uvicorn app.services.integration_api.main:app --port 8080 --reload
python -m app.workers.watcher.main               # in a second shell
```

`scripts/dev-up.sh`, `scripts/dev-down.sh` and `scripts/load-seed.sh` wrap the same steps, and the
`Makefile` exposes `install`, `lint`, `test`, `migrate`, `seed`, `api`, `watcher`, `up`, `down`.

---

## 5. Endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/api/mbox/browse?path=` or `?study&country&site` | user | Browse the MBox tree (path traversal rejected) |
| POST | `/api/transfers/submit` | user | Submit files for archival |
| GET | `/api/transfers?status&study` | user | List transfers |
| GET | `/api/transfers/{transfer_id}` | user | Single transfer |
| POST | `/api/transfers/{transfer_id}/retry` | user | Re-run the pipeline for a transfer |
| GET | `/api/failures?status&reason` | user | Failure queue |
| PUT | `/api/failures/{failure_id}/resolve` | user | Apply a mapping override, re-validate and re-upload |
| PUT | `/api/failures/{failure_id}/escalate` | user | Escalate and assign |
| GET | `/api/mappings` | user | List rules |
| POST | `/api/mappings` | **admin** | Create a rule |
| PUT | `/api/mappings/{mapping_id}` | **admin** | Update a rule (version increments) |
| DELETE | `/api/mappings/{mapping_id}` | **admin** | Deactivate a rule (no hard delete) |
| GET | `/api/audit?from&to&action&performed_by&correlation_id` | user | Query the audit trail |
| GET | `/api/dashboard/stats` | user | Counters, mapping hit rate, aging folders |
| GET | `/api/dashboard/sam-queue` | user | Items awaiting a picklist request |
| POST | `/api/admin/picklists/refresh` | **admin** | Reload the VTMF picklist cache |
| GET/PUT | `/api/admin/sam`, `/api/admin/sam/{sam_id}/status` | **admin** | Manage SAM items |
| GET | `/api/admin/vault/versions` | **admin** | Vault API versions this Vault exposes |
| GET | `/health`, `/health/ready`, `/metrics` | open | Operations |

Interactive docs: `http://localhost:8080/docs`.

Example submission:

```bash
curl -X POST http://localhost:8080/api/transfers/submit \
  -H "Content-Type: application/json" \
  -H "X-Correlation-Id: 2f4c1a9e-8a7d-4d2e-9c1b-7d0f3a5b6c7d" \
  -d '{"files":[{"source_path":"STUDY-001/US/SITE-101/Trial Management/tmf-plan.pdf","initiated_by":"USER"}]}'
```

---

## 6. Processing flow

1. The watcher detects new or modified files, or the UI submits paths directly.
2. The path is normalised and parsed into `Study / Country / Site / Category`. Archive members keep
   the hierarchy of the archive that contains them (`docs.zip!/level-2/inner.zip!/file.pdf`).
3. Pre-checks: extension allow-list, minimum folder depth, non-empty, `MAX_FILE_MB`.
4. SHA-256 is computed by streaming; `(source_path, checksum)` against a prior `SUCCESS` marks the
   transfer `DUPLICATE_SKIPPED` unless `override_duplicate` is set.
5. **Batch preparation runs once, not per file:** the orchestrator authenticates to Vault and
   prefetches the picklists when the cache is empty or older than `PICKLIST_CACHE_TTL_S`. Neither
   step aborts the batch — a Vault outage is downgraded to per-file `VAULT_API_ERROR` failures, and a
   stale cache is safer than skipping validation.
6. The mapping engine resolves metadata by precedence **EXACT → REGEX → STUDY DEFAULT → GLOBAL
   DEFAULT**, then by rule priority. No match ⇒ `failure_queue` with `NO_MAPPING`, status
   `EXCEPTION`, plus a suggested mapping for the reviewer. When `UNMAPPED_POLICY=unclassified` the
   file is *also* filed in VTMF as an Unclassified document and its Vault id recorded — it stays in
   the failure queue either way, because it still needs reclassification.
7. Validation enforces the required fields (`study`, `country`, `site`, `document_type`,
   `document_subtype`, `classification`) and the cached VTMF picklists.
   - Missing required field ⇒ `failure_queue` / `VALIDATION_ERROR`.
   - Value absent from a picklist ⇒ `sam_action_queue`, status `SAM_PENDING`, **no upload**.
8. Upload is idempotent: `external_id__v = SHA-256(source_path | checksum)` is probed with VQL
   first, so a replay returns the existing document instead of creating a duplicate. Files above
   `LARGE_FILE_MB` go to file staging, which switches to a **resumable upload session** above Vault's
   documented 50 MB limit (numbered parts, commit, then poll the commit job).
9. Post-upload verification compares Vault's `size__v` and `md5checksum__v` against the source file.
   Vault reports MD5, so MD5 is computed alongside the SHA-256 used for de-duplication.
10. `transfer_log` and the append-only `audit_trail` are written at every step; metrics and JSON logs
    carry the `correlation_id`.
11. **Batch close:** a digest is produced with success / duplicate / unclassified / SAM / failed
    counts and the top five failure reasons. It is always written to `audit_trail` as a `NOTIFY`
    entry and emailed when `MAIL_ENABLED=true`. The body carries counts and reasons only — never
    document content. A mail outage is logged and never fails the ingestion run.

When anything is ambiguous the pipeline **queues for human action** rather than uploading.

---

## 7. Data model

| Table | Purpose |
| --- | --- |
| `mapping_rules` | Source pattern → target VTMF metadata, versioned, soft-deleted via `is_active` |
| `transfer_log` | One row per file: status, checksum, Vault document id, resolved metadata, `correlation_id` |
| `failure_queue` | Unresolved items with reason, detail, suggested mapping, assignee |
| `audit_trail` | Append-only record of `UPLOAD`, `MAP`, `CLASSIFY`, `RETRY`, `DELETE`, `CONFIG_CHANGE`, `SCAN`, `VALIDATE`, `SAM_REQUEST` |
| `sam_action_queue` | Items blocked on a new VTMF picklist value |
| `picklist_cache` | Local copy of VTMF reference data: study/country/site picklists plus the Type > Subtype > Classification hierarchy |
| `folder_watch` | First-seen timestamp per study folder, drives 60/75-day retention alerts |

Database-level controls:

- `audit_trail` rejects `UPDATE`, `DELETE` and `TRUNCATE` through triggers.
- `mapping_rules` and `transfer_log` maintain `updated_at` through a trigger.
- `CHECK` constraints enforce every status, reason and `initiated_by` enumeration.

---

## 8. Compliance and security

**21 CFR Part 11 / GxP**

- Immutable audit trail enforced by the database, not only by the application.
- Every action is attributable to a user id or the service account, with a timestamp and a
  correlation id linking the full provenance chain from MBox path to Vault document id.
- Configuration changes (mapping rules, picklist refresh) are audited with before/after values.
- Mapping rules are versioned and deactivated rather than deleted.
- ALCOA+: records are attributable, legible, contemporaneous, original and accurate; checksums are
  captured before and verified after transfer.

**Security**

- Least privilege: the MBox service account is read-only; containers run as a non-root user.
- Secrets come from the environment or a secret store and are redacted from logs and audit details.
- Admin endpoints require the `tmf_admin` role from a signed JWT; the local token-minting endpoint is
  disabled unless `DEV_TOKEN_ENABLED=true`.
- Archive handling defends against Zip-Slip traversal, decompression bombs (total size budget and
  per-member compression ratio) and oversized members.
- MBox browsing rejects any path that resolves outside `MBOX_ROOT`.
- VQL parameters are escaped; unhandled exceptions return a generic 500 with details only in logs.
- CORS is off by default; TLS is expected to terminate at the ingress.

---

## 9. Observability

- Structured JSON logs (structlog) with `correlation_id`, `study`, `country`, `site`, `file` and
  `transfer_id`; secret-bearing keys are redacted.
- Prometheus metrics on `/metrics` (API) and port `9101` (watcher):
  - Counters: `transfers_success_total`, `transfers_failed_total`, `failures_by_reason_total`,
    `transfers_duplicate_skipped_total`, `sam_requests_total`, `unclassified_uploads_total`,
    `batch_summary_emails_total`, `picklist_refresh_total`
  - Gauges: `failure_queue_depth`, `sam_queue_depth`, `mapping_hit_rate`, `folder_age_days`,
    `watcher_last_run_timestamp`, `vault_circuit_breaker_open`
  - Histograms: `vault_api_latency_seconds`, `pipeline_latency_seconds`
- Alert rules in `deploy/alerts.yml`: folder age ≥ 60 and ≥ 75 days, failure-queue backlog, open
  circuit breaker, stalled watcher.
- Health: `/health` (liveness) and `/health/ready` (database reachability).

---

## 10. Testing and validation

```bash
pytest                      # unit + integration
pytest -m integration       # Testcontainers PostgreSQL (requires Docker)
ruff check app tests scripts db
```

| Suite | Covers |
| --- | --- |
| `test_path_parser.py` | Hierarchy parsing, root stripping, nested archive paths |
| `test_archive.py` | Nested extraction, depth limits, corrupt archives, Zip-Slip, size limits |
| `test_hashing.py` | Streamed checksums, deterministic external ids |
| `test_mapping.py` | Precedence, priority, overrides, malformed regex |
| `test_validation.py` | Required fields versus picklist misses (SAM routing) |
| `test_orchestrator.py` | Success, `NO_MAPPING`, `SAM_PENDING`, duplicates, idempotency, nested ZIPs, corrupt archives, pre-checks |
| `test_vault_client.py` | Retry on 429, no retry on 4xx, circuit breaker, session handling |
| `test_watcher.py` | Discovery, archive expansion, duplicate suppression, folder-age tracking |
| `test_api.py` | OpenAPI contract, browse, submit, failure resolution, mapping CRUD, dashboard |
| `test_security.py` | 401/403/201 for admin RBAC, tampered tokens, dev-token endpoint disabled |
| `test_audit_immutability.py` | Database-level rejection of `UPDATE`/`DELETE` on `audit_trail` |

**Validation artifacts**

- **IQ** – `docker compose up` reaches healthy state; `alembic upgrade head` applies `V001_init`;
  `/health/ready` returns 200; the watcher registers its scheduled job; CI builds the image.
- **OQ** – the acceptance criteria in section 12, each backed by a named test above; the CI job also
  proves the audit trail rejects `DELETE` against a live PostgreSQL instance.
- **PQ** – throughput of ≥ 100 documents/hour and a median end-to-end latency under 60 minutes,
  measured from `pipeline_latency_seconds` and `transfers_success_total` in the target environment.

---

## 11. Operations runbook

| Situation | Action |
| --- | --- |
| Rotate Vault credentials | Update the secret store, restart `api` and `watcher`; the client re-authenticates on the next call |
| Circuit breaker open | Check `vault_circuit_breaker_open` and Vault status; the breaker half-opens after `CIRCUIT_BREAKER_RESET_S`, then retry the affected transfers |
| Reprocess failures | `GET /api/failures?status=OPEN`, then `PUT /api/failures/{id}/resolve` with a mapping override (optionally `persist_as_rule`) |
| Mapping change | Create or update through the admin endpoints; rules are reloaded per batch, so no restart is required |
| New picklist value approved | Complete the SAM item, `POST /api/admin/picklists/refresh`, then retry the `SAM_PENDING` transfers |
| Folder approaching 90-day retention | Act on the `MBoxFolderApproachingRetention` / `MBoxFolderCriticalRetention` alerts and drain the study before deletion |
| Corrupt archive | The file is queued as `CORRUPT_ARCHIVE`; obtain a clean copy from the study team and resubmit |

---

## 12. Acceptance criteria

- Correctly mapped documents are created in VTMF with the expected metadata and their Vault ids are
  recorded in `transfer_log`.
- Unmapped or invalid files land in `failure_queue` with a reason and status `EXCEPTION`, and are
  fully auditable.
- Values missing from VTMF picklists land in `sam_action_queue` with status `SAM_PENDING`; no upload
  is attempted.
- Nested ZIPs are extracted to the configured depth; corrupt archives are queued, never dropped.
- Duplicates are skipped unless explicitly overridden, and replays are idempotent in Vault.
- Metrics are populated, health endpoints are green and JSON logs carry the correlation id.
- The audit trail is immutable at the database level.

---

## 13. Roadmap (post-MVP)

- Replace the direct watcher → API call with a durable queue (Celery/Redis or a managed broker).
- Mapping management UI with a maker-checker approval workflow.
- Scheduled picklist synchronisation from VTMF.
- SFTP and cloud-storage MBox sources behind the existing source abstraction.
- Bulk Box → VTMF migration utility, subject to approval.
