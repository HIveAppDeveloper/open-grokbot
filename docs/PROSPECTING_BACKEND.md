# Persisted public-source prospecting

This backend adds an authenticated, account-isolated discovery path without a model or paid search call. The new `/prospecting` screen starts with a company website, requires editable confirmation of the extracted offer and target market, and displays persisted company candidates, source timestamps, audit events and app drafts. It supplements the existing Cipher frontend; it does not replace Cipher routes, provider code, or plugin authorization.

## API

- `POST /api/prospecting/profile` with `{url}` returns website copy and editable market/product suggestions that require confirmation; authentication is required. It allows public HTTPS hostnames on port443 only, rejects mixed/private DNS answers, pins the validated IP while preserving TLS hostname verification, checks robots rules, refuses redirects, bounds time/bytes and strips scripts. It extracts copy rather than claiming an inferred ideal customer profile.
- `POST /api/prospecting/runs` with `{market,product,limit}` queues a SQL-owned run (HTTP 202). Market and product are required; limit is 1–20, default 5. At most three active runs per account are accepted.
- `GET /api/prospecting/runs` lists the most recent 50 runs for the signed-in account.
- `GET /api/prospecting/runs/{id}` returns persisted status, results, audit events, current Gmail metadata, limitations, and the disabled sending gate.
- `POST /api/prospecting/runs/{id}/cancel` requests cancellation; the worker stops at the next request boundary and retains retrieved evidence.
- `POST /api/prospecting/runs/{id}/results/{result_id}/draft` with `{sender_name}` creates a review-required draft in the application database. It does not create a Gmail draft, resolve a recipient, or send a message.

Run statuses are `pending`, `running`, `cancel_requested`, `cancelled`, `completed`, `partial`, and `failed`. Empty completed results mean the public search returned no qualifying organization descriptions. Network failures never create substitute/demo companies. A failed later source read retains earlier results and ends as `partial`.

A result contains `id`, `name`, `source_url`, `retrieved_at`, `observed.description` (direct source copy), `inferred.fit_score`, matched terms and per-term source-backed contributions, `contact: {status: "needed", email: null, source_url: null}`, and an optional persisted draft. The score is lexical relevance only; no demand, buying intent, spend, current employment, or contact details are inferred.

## Source and worker behavior

The existing single lock-owning worker executes one discovery or extraction slice per loop, committing candidates, cursor, results, and audit events after each read. Existing agent tasks still get a scheduling opportunity between slices. Restart changes interrupted research to pending and resumes the saved cursor; result IDs deduplicate repeated reads. PostgreSQL commit notifications wake queued work. SQL owns all state.

Only the fixed public Wikipedia API is requested. Search uses the confirmed market plus `company`; extraction reads introductory plaintext. Redirects are refused. The shared public HTTPS/DNS validator rejects private addresses. Each request has a 12-second total limit, an 8-second HTTP timeout, and a 512 KiB decoded response cap. Reads are paced at no more than one per second in the single worker. HTTP 429 records a safe `rate_limited` event and available Retry-After; there is no hidden retry loop. Authentication, authorization, and upstream diagnostics/tokens are never copied into results.

Gmail state is scoped to the current account's saved enabled plugin metadata. A saved Gmail browser integration reports `connection_saved`, `verified:false`, and `supported_draft:false`. No OAuth flow, credential, mailbox operation, or provider tool is invoked. All app drafts remain review-required, contact remains needed, and sending is disabled.

## Validation and deployment

`tests/test_prospecting.py` covers the actual FastAPI application's auth middleware and routes, SQL persistence, account isolation, durable worker slices, deduplication, lexical evidence, missing contacts, app drafts, cancellation, restart recovery, partial/rate-limited results, queue/input bounds, redirect/error handling, response size, and timeouts. Fixtures are synthetic and are never presented as live research.

`verification/prospecting-live.json` records a separate real public Wikipedia run through the API, disposable SQLite database, and worker slices. It uses a synthetic account and no production data or credentials.

Existing root `vercel.json` defines the backend container, frontend service binding, and `agent-worker` daemon (`python -m app.worker_main`). The API and worker must both receive the change. Startup bootstrap creates the new tenant-scoped table under the existing advisory lock; no existing records are migrated. A frontend-only deploy cannot execute this Python worker. Runs and drafts remain in the existing app database; profile responses are not automatically retained there. No new storage, credentials, OAuth grants, model calls or outreach are required for discovery.

## Email connection limits

The existing Google login verifies identity tokens and does not obtain Gmail authorization. The saved `gmail.com` connection exposes generic browser tools, not a typed Gmail draft API. The UI therefore distinguishes an application draft from an **Open Gmail compose** link. It does not claim either saved Gmail drafts or sent email.

Gmail API draft creation requires a separately configured authorization-code client/redirect, enabled Gmail API, encrypted per-user refresh grants and explicit consent for the restricted `gmail.compose` scope. That scope also permits sending; a draft-only UI does not narrow the credential. Applicable Google verification must be resolved before public activation. See https://developers.google.com/workspace/gmail/api/auth/scopes and https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create .

Resend publishes an OAuth MCP server at https://mcp.resend.com/mcp . Existing generic OAuth cannot silently be treated as a narrow email grant: the official client guide requires explicit scopes (omission grants both `emails:send` and `full_access`) and rotating-refresh-token handling. Per-grant refresh serialization, authorized scope and owned verified sender setup need verification before activation. The global login-mail key is not tenant outreach authority. See https://resend.com/docs/guides/building-a-resend-oauth-client .
