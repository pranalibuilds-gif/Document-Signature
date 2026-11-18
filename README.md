# Papertrail — Document Signature Platform

Papertrail is a full-stack electronic-signature MVP built with FastAPI and
React. A sender uploads a PDF, places signature fields, and invites a recipient
to sign or reject it through a secure link. The application stores documents
privately, records workflow events, and produces a separately stored signed PDF.

> **Signature scope:** Papertrail applies a visual electronic-signature stamp
> and verifies stored files with SHA-256 hashes. It does not create a
> certificate-backed PAdES/PKCS#7 digital signature and is not, by itself, a
> compliance certification or legal opinion.

## Contents

- [Architecture](#architecture)
- [Capabilities and security](#capabilities-and-security)
- [Technology](#technology)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [API overview](#api-overview)
- [Tests](#tests)
- [Deployment](#deployment)
- [Production checklist](#production-checklist)

## Architecture

```text
 React + TypeScript SPA
       │  HTTPS, Bearer JWT or X-Signing-Token
       ▼
 FastAPI routers ── Pydantic request/response schemas
       │
       ├── PDF service       PyMuPDF inspection and visual stamping
       ├── Storage service   local disk or S3-compatible object storage
       ├── Email service     console or Resend
       └── Audit service     append events through application workflows
       │
 SQLAlchemy 2.0 models and sessions
       │
 PostgreSQL
```

The sender flow is: authenticate → upload a PDF → place normalized signature
fields → dispatch an invitation. The guest flow loads the invitation through a
token header, then completes or rejects it. A completed signing stores a
separate signed PDF; the original upload is retained.

## Capabilities and security

- **Authentication and ownership:** Sender endpoints require a bearer JWT and
  enforce document ownership. Passwords are bcrypt-hashed, with UTF-8 byte
  length considered for bcrypt's input limit.
- **Signing invitations:** Tokens are generated with cryptographic randomness.
  The raw token is delivered in a URL fragment and is returned only during
  dispatch; the database stores its SHA-256 hash. The frontend removes the
  fragment from the address bar before using the token in the
  `X-Signing-Token` header.
- **Idempotent viewing and single use:** Guest `GET` requests do not consume the
  invitation. Completion and rejection validate and revoke it; concurrent
  completion is serialized using row locking on PostgreSQL and an immediate
  transaction on SQLite.
- **PDF safety and placement:** Uploads are bounded to 10 MiB and checked
  before storage; encrypted PDFs are rejected. Signature fields use normalized
  page coordinates. Stamping accounts for page CropBoxes and rotations of
  0°, 90°, 180°, and 270°.
- **Signature image bounds:** Guest signature images are validated PNG data
  with size and dimension limits before decoding and stamping.
- **File integrity and cleanup:** Original and signed PDFs have stored
  SHA-256 digests checked on read. Storage cleanup is attempted if a database
  transaction fails after a file is written.
- **Audit and request metadata:** Workflow events are recorded and exposed as a
  chronological audit trail. Client IP forwarding is ignored unless
  `TRUST_PROXY_HEADERS=true`; enable it only behind a proxy whose forwarding
  behavior is trusted. The application API has no audit-event update/delete
  operation; this is not a tamper-proof or WORM audit store.
- **HTTP boundaries:** Production settings require explicit HTTPS frontend
  origins, and the API adds `X-Content-Type-Options`, `X-Frame-Options`, and
  `Referrer-Policy` response headers.
- **Private storage and email:** Local disk is intended for development.
  Production supports AWS S3 or Supabase's S3-compatible API. Buckets must be
  private; the API streams files after authorization and integrity checks.
  Resend is used for production email, while the development console adapter
  omits the signing URL from its log message.

SHA-256 checks detect a file that differs from its stored digest; they do not
protect against an administrator who can change both the file and database.
Use external immutable audit retention and a certificate-backed signing
solution if those guarantees are required.

## Technology

| Area | Technologies |
| --- | --- |
| Backend | Python, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic |
| PDF and security | PyMuPDF, Pillow, PyJWT, bcrypt, SHA-256 |
| Database | PostgreSQL (local Compose uses version 16) |
| Storage and email | Local disk / S3-compatible storage; console / Resend |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, React PDF |
| Tests | pytest, FastAPI TestClient, mocked object storage |

## Repository layout

```text
.
├── backend/
│   ├── alembic/                  # Migration environment and revisions
│   ├── app/
│   │   ├── core/                 # Configuration, DB sessions, security
│   │   ├── models/               # SQLAlchemy entities
│   │   ├── routers/              # Auth, documents, audit, guest signing
│   │   ├── schemas/              # API validation and response models
│   │   ├── services/             # Audit, email, PDF, storage
│   │   └── utils/                # Request metadata helpers
│   ├── tests/                    # Backend unit and API tests
│   ├── .env.example
│   ├── railway.toml
│   └── requirements.txt
├── frontend/
│   ├── public/                   # Static assets and Netlify SPA fallback
│   ├── src/
│   │   ├── api/                  # Typed fetch client and API types
│   │   ├── auth/                 # React authentication context/hooks
│   │   ├── components/           # Shared UI and signature pad
│   │   └── pages/                # Login, dashboard, preparation, guest signing
│   ├── vercel.json
│   └── package.json
├── docker-compose.yml            # Local PostgreSQL 16
└── render.yaml                   # Render backend service
```

## Local development

### 1. Start PostgreSQL

From the repository root:

```powershell
$env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString('N')
docker compose up -d
```

Compose requires `POSTGRES_PASSWORD` and exposes PostgreSQL on port `5432`.
Use the same password in `DATABASE_URL` in `backend/.env`.
On macOS/Linux, set it with
`export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"`
before running Compose.

### 2. Configure and run the backend

```powershell
cd backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
python -m pytest
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On macOS/Linux, activate the environment with
`source .venv/bin/activate` and copy the example using
`[ -f .env ] || cp .env.example .env`.
The default `DATABASE_URL` in the example matches the Compose service. Change
`JWT_SECRET_KEY` for any environment beyond local development.

### 3. Run the frontend

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

If PowerShell blocks the `npm` script, use `npm.cmd ci` and `npm.cmd run dev`.
Vite serves the app at `http://127.0.0.1:5173` and proxies `/api` requests to
the local backend. The API health check is
`http://127.0.0.1:8000/api/health`; interactive API documentation is at
`http://127.0.0.1:8000/docs`.

The frontend's default API base is `/api`. Set `VITE_API_BASE_URL` at build
time to a backend origin that includes the `/api` prefix when deploying it
separately.

## API overview

Sender endpoints require `Authorization: Bearer <access-token>`. Guest
endpoints use `X-Signing-Token: <raw-token>`; the frontend's normal guest flow
does not put the token in the API URL.

| Method | Endpoint | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health` | Public | Database connectivity check |
| `POST` | `/api/auth/register` | Public | Create a sender account |
| `POST` | `/api/auth/login` | Public | Authenticate and issue a JWT |
| `GET` | `/api/auth/me` | Sender | Read the current sender profile |
| `POST` | `/api/docs/upload` | Sender | Upload and inspect a PDF |
| `GET` | `/api/docs` | Sender | List owned documents; optional `?status=` filter |
| `GET` | `/api/docs/{document_id}` | Sender | Read owned document metadata |
| `GET` | `/api/docs/{document_id}/file` | Sender | Stream a verified PDF; choose `?version=original` or `signed` |
| `GET` | `/api/docs/{document_id}/fields` | Sender | List signature fields |
| `PUT` | `/api/docs/{document_id}/fields` | Sender | Replace fields on a draft |
| `POST` | `/api/docs/{document_id}/send` | Sender | Dispatch or re-dispatch an invitation |
| `GET` | `/api/audit/{document_id}` | Sender | Read the chronological audit history |
| `GET` | `/api/public/sign` | Guest | Read invitation details and fields |
| `GET` | `/api/public/sign/file` | Guest | Stream the verified original PDF |
| `POST` | `/api/public/sign/complete` | Guest | Submit signature images and complete signing |
| `POST` | `/api/public/sign/reject` | Guest | Reject with a reason |

The guest endpoints above require the signing-token header. Token-in-path
compatibility routes are also retained, but the frontend uses header-based
routes. Uploads are limited to 10 MiB; signature PNGs are limited to 500 KiB
each.

## Tests

Run backend tests and dependency validation from `backend/`:

```powershell
python -m pytest
python -m pip check
```

Run frontend production checks from `frontend/`:

```powershell
npm run build
npm run lint
```

The backend tests cover authentication, ownership, file handling, dispatch and
token rotation, guest signing, audit events, coordinate stamping, and
concurrent completion. S3 behavior is tested with a mocked client; live cloud
providers are not contacted by the test suite.

## Deployment

### Backend: Render or Railway

The repository includes [`render.yaml`](./render.yaml) and
[`backend/railway.toml`](./backend/railway.toml). Both configure PostgreSQL
backends, migration-before-start commands, Uvicorn, and `/api/health` health
checks. Set the backend root directory to `backend/` on Railway.

Set production environment variables in the hosting provider, not in source
control. At minimum configure:

- `ENVIRONMENT=production`
- `DATABASE_URL` for the runtime application database role
- `MIGRATION_DATABASE_URL` for a separate schema-migration role when available
- `JWT_SECRET_KEY` with a unique random value of at least 32 characters
- `CORS_ALLOWED_ORIGINS` as a JSON array containing only the HTTPS frontend
  origin
- `FRONTEND_BASE_URL` as that same HTTPS origin
- `STORAGE_BACKEND=s3` or `supabase`, bucket and provider settings
- `EMAIL_BACKEND=resend`, `RESEND_API_KEY`, and a verified
  `EMAIL_FROM_ADDRESS`

The app role should have only the permissions needed at runtime; the migration
role needs DDL privileges. The application does not inspect live database
grants, so verify role permissions directly with your database provider.
Configure an encrypted, private bucket with public access blocked. The Render
Blueprint defaults to AWS S3; for Supabase Storage, select
`STORAGE_BACKEND=supabase` and set `S3_ENDPOINT_URL` and Supabase S3
credentials.

The deployment templates run migrations at startup. Keep one backend instance
while using that strategy; before scaling horizontally, run Alembic from a
single release/deploy job so replicas cannot race on schema changes. Enable
`TRUST_PROXY_HEADERS=true` only after verifying that the hosting proxy safely
sets the forwarded client address.

### Frontend: Vercel or Netlify

- **Vercel:** Set the project root to `frontend/`, build with `npm ci` and
  `npm run build`, and publish `dist/`. [`frontend/vercel.json`](./frontend/vercel.json)
  rewrites client-side routes to the SPA entry point.
- **Netlify:** Set the base directory to `frontend/`, build with `npm ci` and
  `npm run build`, and publish `dist/`. `frontend/public/_redirects` supplies
  SPA route fallback.

Set `VITE_API_BASE_URL` to the deployed backend origin ending in `/api`, then
allow the frontend's exact HTTPS origin in the backend CORS configuration.

Review [`backend/.env.example`](./backend/.env.example) for the complete
configuration list. Production settings reject SQLite, local storage, console
email, sample JWT secrets, and wildcard or non-HTTPS frontend origins. Live
PostgreSQL, object-storage, email-provider, and deployment checks require
provisioned services and credentials.
