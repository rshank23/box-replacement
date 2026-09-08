# Frontend — Document Migration Utility

React + TypeScript + Vite UI for the MBox → Veeva Vault TMF backend.

## Pages

| Route | Purpose | API used |
| --- | --- | --- |
| `/login` | J&J SSO entry point; local development token sign-in | `POST /api/admin/dev-token` |
| `/dashboard` | Migration overview, mapping coverage, retention watch, recent jobs | `/api/dashboard/stats`, `/api/transfers` |
| `/upload` | Browse the MBox tree, select files, trigger a batch | `/api/mbox/browse`, `/api/transfers/submit` |
| `/mapping/:correlationId` | Resolved metadata per job; edit mappings, re-process | `/api/transfers`, `/api/failures`, `/api/transfers/{id}/retry` |
| `/unclassified` | Failure queue triage: assign metadata, notify CRO, export | `/api/failures`, `/api/failures/{id}/resolve`, `/…/escalate` |
| `/audit` | Immutable audit trail with filters and CSV/PDF export | `/api/audit` |
| `/notifications` | Retention alerts, SAM action queue, open exceptions | `/api/dashboard/stats`, `/api/dashboard/sam-queue` |
| `/settings` | Mapping rule CRUD, picklist cache refresh, session info | `/api/mappings`, `/api/admin/picklists/refresh` |

A "job" is derived client-side by grouping transfers on their `correlation_id`, which is the same
identifier the backend writes into every audit entry.

## Run

```bash
cp .env.example .env
npm install
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8080
```

Other scripts: `npm run build`, `npm run preview`, `npm run typecheck`.

The dev server and the production nginx image both reverse-proxy `/api`, so the browser stays
same-origin and CORS remains disabled on the Integration API.

## Configuration

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Absolute API base. Leave empty to use the proxy. |
| `VITE_API_PROXY_TARGET` | Dev-server proxy target. |
| `VITE_SSO_LOGIN_URL` | Enterprise SSO entry point. The SSO button is disabled when unset. |
| `VITE_ENVIRONMENT_LABEL` | Environment chip shown in the header. |

## Auth

The bearer token lives in `sessionStorage` only and is cleared on sign-out or on any `401`. Local
sign-in calls the API's development token endpoint, which is available only when
`DEV_TOKEN_ENABLED=true`; when it is off the UI falls back to unauthenticated access and shows an
"Auth disabled on API" warning so the state is never ambiguous. Admin-only controls are disabled for
users without the `tmf_admin` role, and the API enforces the same check server-side.

## Docker

```bash
docker compose -f ../deploy/docker-compose.yml up -d --build frontend
# http://localhost:3000
```
