# Veeva Vault API Integration — Analysis and Implementation Report

Source of truth: **[Vault API v26.2](https://general.veevavault.dev/vault-api/api-reference/26.2/)**
(current GA at time of writing). Reviewed sections: Getting Started, Authentication, Documents,
File Staging, Picklists, VQL, Jobs, and Document Type metadata.

This document records what the API actually requires, what our integration had wrong, what was
changed, and what was deliberately left out.

---

## 1. Executive summary

Reviewing the live API reference against our client found **six correctness defects** that would
have failed against a real Vault, and **six missing capabilities**. All twelve are now addressed.

The most serious findings:

| # | Severity | Finding |
| --- | --- | --- |
| 1 | **Critical** | Files over 50 MB would have been rejected. Simple file staging is capped at 50 MB; larger files require a resumable upload session. Our `LARGE_FILE_MB=50` threshold routed files to a staging call that Vault would refuse for exactly the files it was meant to handle. |
| 2 | **Critical** | Document fields used non-existent names (`external_id__c`, `checksum__c`, `source_path__c`, `country__v`). Vault would have rejected the create call or silently dropped the fields, breaking idempotency. |
| 3 | **High** | Post-upload verification read `size` and `md5checksum__v` off the wrong object shape, so **verification never actually ran**. Vault returns `size__v` on the `document` object. |
| 4 | **High** | `type__v` / `subtype__v` / `classification__v` were validated against *picklists*. They are not picklists — they are the document type hierarchy, retrieved from a different API. Validation was passing everything through unchecked. |
| 5 | **Medium** | Unclassified documents were sent with a TMF lifecycle. Vault requires the `Inbox` lifecycle with the `Unclassified` type and ignores all other fields except the eTMF set. |
| 6 | **Medium** | No protection against Vault *authentication defaulting* — Vault can silently authenticate you against a different Vault when the intended one is inactive. We could have archived a study into the wrong Vault. |

---

## 2. Endpoint inventory

Every endpoint the backend now calls, with its purpose.

| Endpoint | Method | Used for |
| --- | --- | --- |
| `/api/{v}/auth` | POST | User name + password session |
| `/api/{v}/keep-alive` | POST | Refresh an idle session |
| `/api/{v}/session` | DELETE | End session on shutdown |
| `/api/` | GET | Retrieve API versions (token validation, IQ evidence) |
| `/api/{v}/query` | POST | VQL idempotency probe on `external_id__v` |
| `/api/{v}/objects/documents` | POST | Create Single Document |
| `/api/{v}/objects/documents/{id}` | GET | Retrieve Document (post-upload verification) |
| `/api/{v}/objects/picklists/{name}` | GET | Retrieve Picklist Values (study, country, site) |
| `/api/{v}/metadata/objects/documents/types` | GET | Retrieve All Document Types |
| `/api/{v}/metadata/objects/documents/types/{t}` | GET | Retrieve Document Type → subtypes |
| `/api/{v}/metadata/objects/documents/types/{t}/subtypes/{s}` | GET | Retrieve Document Subtype → classifications |
| `/api/{v}/services/file_staging/items` | POST | Create File (≤ 50 MB) |
| `/api/{v}/services/file_staging/upload` | POST | Create Resumable Upload Session (> 50 MB) |
| `/api/{v}/services/file_staging/upload/{id}` | PUT | Upload File Part |
| `/api/{v}/services/file_staging/upload/{id}` | POST | Commit Upload Session |
| `/api/{v}/services/file_staging/upload/{id}` | DELETE | Abort Upload Session (on failure) |
| `/api/{v}/services/jobs/{job_id}` | GET | Retrieve Job Status (commit completion) |

---

## 3. Findings and changes in detail

### 3.1 Authentication — API access tokens added

**What the docs say.** Veeva explicitly recommends API access tokens over username/password:
> "We recommend using an API access token to authenticate to Vault API."

Token values begin with `veeva-vault-` and go in `Authorization: Bearer {token}`. Session IDs go in
`Authorization: {sessionId}` — **without** the `Bearer` keyword (though `Bearer {sessionId}` is also
accepted). Tokens are valid up to 90 days; sessions expire on inactivity with a hard 48-hour ceiling.

**What we had.** Username/password only, one `/auth` call per file.

**What changed.** `VAULT_AUTH_MODE=token|password`. In token mode no `/auth` call is made at all and
the token is sent as a bearer credential; the token is validated once against `GET /api/`. In
password mode the session is cached and re-established before the 48-hour ceiling.

### 3.2 Authentication defaulting — new safety check

**What the docs say.**
> "Rarely, you can authenticate against a different Vault if the intended Vault is inactive… As a
> best practice, you should always check the returned `vaultId` against the `vaultIds` in the list."

**Why this matters here.** Silently archiving a completed study's TMF into the wrong Vault is a
data-integrity incident, and one that would be discovered late.

**What changed.** `VAULT_EXPECTED_VAULT_ID` — when set, a mismatch raises `VaultAuthError` and the
batch fails closed rather than uploading anywhere.

### 3.3 Session lifecycle — keep-alive and end session

Added `POST /keep-alive` and `DELETE /session`. The client tracks last activity so an idle session
can be refreshed rather than re-authenticated, and the session is ended on service shutdown instead
of being left to time out.

### 3.4 File staging — resumable upload sessions (the critical fix)

**What the docs say.** Create Folder or File: *"The maximum allowed file size is 50MB."* Larger files
need a three-step resumable session:

1. `POST /services/file_staging/upload` → `upload_session_id` (max file size 500 GB)
2. `PUT /services/file_staging/upload/{id}` per part, with `X-VaultAPI-FilePartNumber`,
   `Content-Type: application/octet-stream`. Parts must be **5–52 MB**, equal size except the last,
   and uploaded **in numerical order**.
3. `POST /services/file_staging/upload/{id}` to commit → returns a `job_id`.

Vaults allow **50 active sessions**; uncommitted sessions expire after 72 hours.

**What we had.** A single multipart POST of the whole file to `/items`, regardless of size.

**What changed.** `stage_file()` now branches on the documented 50 MB limit and performs the full
session flow, streaming parts so memory stays constant. `VAULT_FILE_PART_MB` (default 25) is clamped
to the legal 5–52 MB range. **If any part fails, the session is aborted** via `DELETE` so it does not
consume one of the 50 slots until it expires.

### 3.5 Jobs — commit is asynchronous

**What the docs say.** Commit returns a `job_id`, and the file is only available on the staging
server once the job completes. Job Status *"can only be requested once every 10 seconds for each
`job_id`. When this limit is reached, Vault returns `API_LIMIT_EXCEEDED`."*

**What changed.** `await_job()` polls at exactly the documented 10-second interval and treats
`ERRORS_ENCOUNTERED`, `CANCELLED`, `TIMEOUT` and `MISSED_SCHEDULE` as failures. `API_LIMIT_EXCEEDED`
was already mapped to our retryable rate-limit path.

### 3.6 Document fields — corrected to real Vault names

| Purpose | Was | Now | Note |
| --- | --- | --- | --- |
| Idempotency key | `external_id__c` | `external_id__v` | Standard Vault field, shown in the Create Single Document example |
| Country | `country__v` | `study_country__v` | The eTMF field; `country__v` is a different picklist field |
| Checksum | `checksum__c` | *(removed)* | Invented field; Vault supplies `md5checksum__v` on read |
| Source path | `source_path__c` | *(removed)* | Invented field; provenance lives in our audit trail |
| Size (read) | `size` | `size__v` | Field is on the `document` object |
| Checksum (read) | `md5checksum__v` | `md5checksum__v` | Was correct, but read from the wrong object |

Field names are now defined once as constants in `app/libs/common/vault_client.py`.

### 3.7 Post-upload verification actually works now

Vault returns **MD5** (`md5checksum__v`); we hold **SHA-256** for de-duplication. The previous code
compared digests only when their lengths matched, which for a real Vault is *never* — verification
silently did nothing. We now compute MD5 alongside SHA-256 and compare like for like. SHA-256 remains
the de-duplication and idempotency digest.

### 3.8 Document type hierarchy is not a picklist

**What the docs say.**
> "Document type refers … to the structure of hierarchical fields (Type > Subtype > Classification)"

These are retrieved from `/metadata/objects/documents/types/...`, not `/objects/picklists/`.

**What changed.** `refresh_reference_data()` now sources study/country/site from picklists and
type/subtype/classification by walking the type hierarchy. This makes the SAM routing genuinely
correct: a document subtype that VTMF does not have configured is now detected instead of being
waved through.

### 3.9 Unclassified documents — corrected shape

**What the docs say.** Unclassified documents take `type__v = Unclassified` (`undefined__v`) and
`lifecycle__v = Inbox` (`unclassified__v`). In eTMF Vaults you may additionally set `product__v`,
`study__v`, `study_country__v`, `site__v` — *"Any other fields included in the input will be
ignored."*

**What changed.** The unmapped-document fallback now sends the `Inbox` lifecycle and filters the
payload to exactly the allowed eTMF field set, rather than sending a TMF lifecycle and a full
metadata payload that Vault would have discarded.

### 3.10 Migration mode — new, and well suited to this use case

**What the docs say.** `X-VaultAPI-MigrationMode` lets you set `status__v`, control name/document
number/version, and *"bypasses entry criteria, entry actions, and event actions and does not send
notifications."* Requires the Document Migration permission. `X-VaultAPI-NoTriggers` additionally
bypasses doctype triggers.

**Why it fits.** Post-study archival is a bulk migration of already-approved documents. Firing
lifecycle entry actions and notifying users for thousands of historical documents is noise, and
notification storms are a real operational risk during a large study archival.

**What changed.** `VAULT_MIGRATION_MODE` and `VAULT_NO_TRIGGERS`, both **off by default** since they
require an elevated permission and change lifecycle semantics.

### 3.11 API version

Default moved from `v25.1` to **`v26.2`** (current GA). Versions other than the newest are frozen, so
pinning is safe; `GET /api/` is available to confirm what a given Vault exposes.

### 3.12 Error handling refinements

`INVALID_SESSION_ID` now raises `VaultAuthError` (triggering re-authentication) rather than being
treated as a generic request failure. Vault `FAILURE` payload messages are surfaced in the exception
text, so the failure queue shows Vault's own reason rather than a generic message.

---

## 4. What was deliberately not implemented

| Capability | Why not |
| --- | --- |
| **Create Multiple Documents** (bulk CSV) | Genuinely recommended by Veeva for >1 document and the clearest remaining throughput win. It needs CSV assembly, per-row result parsing, and partial-failure reconciliation back into `transfer_log`. That is a substantial change and belongs in its own piece of work, not bundled into a correctness pass. Our current per-file loop is correct, just chattier. |
| **Vault Loader** | Optimised for very large migrations but asynchronous and file-based; the per-file audit granularity we need for Part 11 is harder to preserve. |
| **Binders** | TMF structure in Vault is driven by the EDL/milestone model here; we archive documents, not binder hierarchies. |
| **Renditions** | Vault generates viewable renditions automatically. We have no source renditions to upload. |
| **Document Roles / sharing** | Access is governed by Vault's configured security model; the integration should not be granting access. |
| **Document Versions** | Every archived file is a new document with a stable external id. Versioning would need a business rule that does not exist yet. |
| **Delegated / OAuth2 / SAML auth** | Worth adopting when the corporate IdP is wired in; API access tokens cover the service-account case today. |
| **VQL pagination** | Our only query returns at most one row (idempotency probe). Pagination would be dead code. |

---

## 5. Changes by file

| File | Change |
| --- | --- |
| `app/libs/common/vault_client.py` | Rewritten. Vault field-name constants, documented limits, expanded `VaultClient` protocol, `VaultJobError`; `FakeVaultClient` now models the type hierarchy, keep-alive and the 50 MB split |
| `app/libs/common/vault_client_veeva.py` | Rewritten against v26.2: token auth, vault-id verification, keep-alive/end-session, resumable uploads, job polling, type hierarchy, corrected field parsing |
| `app/libs/common/config.py` | `VAULT_AUTH_MODE`, `VAULT_API_TOKEN`, `VAULT_EXPECTED_VAULT_ID`, `VAULT_MIGRATION_MODE`, `VAULT_NO_TRIGGERS`, `VAULT_FILE_PART_MB`, `VAULT_JOB_TIMEOUT_S`, `UNCLASSIFIED_LIFECYCLE`, `DOCUMENT_LIFECYCLE`; API version default `v26.2` |
| `app/libs/common/hashing.py` | `md5_file()` for Vault checksum verification |
| `app/libs/common/vault_factory.py` | Ends the Vault session on shutdown |
| `app/services/integration_api/domain/validation.py` | Split reference data into picklist-backed and hierarchy-backed; `refresh_reference_data()` |
| `app/services/integration_api/domain/orchestrator.py` | Correct Vault field mapping, Unclassified field filtering, MD5 verification |
| `app/services/integration_api/routers/admin.py` | Refresh endpoint now reloads full reference data; added `GET /api/admin/vault/versions` |
| `tests/test_vault_api.py` | New — 17 contract tests against a mocked Vault |
| `.env.example`, `backend.md` | Documentation |

---

## 6. Verification

```
pytest -q      # 79 passed, 2 skipped
ruff check     # clean
```

`tests/test_vault_api.py` asserts against `httpx.MockTransport` doubles of the documented
request/response shapes, covering:

- session vs bearer credential placement, and that token mode never calls `/auth`
- authentication defaulting is refused when the returned `vaultId` is unexpected
- keep-alive and end-session hit the documented paths
- `INVALID_SESSION_ID` surfaces as an auth error
- migration-mode headers are sent only when enabled
- `size__v` / `md5checksum__v` / `status__v` parsing
- small files use `/items`; large files create a session, upload numbered parts, commit, and poll the job
- a failed part upload aborts the session
- the type hierarchy is walked types → subtypes → classifications
- part size is clamped to the documented 5–52 MB range

---

## 7. Residual risk

These cannot be closed without a real Vault sandbox:

1. **Document type/subtype/classification API names vs labels.** The metadata API returns both; we
   cache labels because that is what mapping rules are written against. If a Vault's labels are
   ambiguous, mapping should switch to API names.
2. **Required-field discovery.** `Retrieve Document Type` returns `properties[]` with `required`
   flags per type. We still validate against our own fixed `REQUIRED_FIELDS` list. Deriving required
   fields per document type from Vault is the natural next step and would remove a hard-coded
   assumption.
3. **Burst limits.** Veeva applies burst and daily API limits reported in response headers. We rate
   limit client-side and retry on `API_LIMIT_EXCEEDED`, but do not yet read the remaining-quota
   headers to throttle pre-emptively.
4. **eTMF field names.** `study__v`, `study_country__v` and `site__v` are documented for eTMF Vaults;
   a specific Vault's configuration should be confirmed before go-live.
