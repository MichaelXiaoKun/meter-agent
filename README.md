# bluebot meter agent

Conversational assistant for bluebot ultrasonic flow meters: orchestrator (Claude + tools), React web UI, and subprocess agents for flow analysis, meter status, and pipe configuration.

This document covers **running the stack locally** and **configuring environment variables**. For line-by-line variable comments, see [`.env.example`](.env.example).

---

## What you need

- **Python 3.13+** (matches the [Dockerfile](Dockerfile))
- **Node.js 20+** and npm (for the Vite frontend in dev)
- **Anthropic API access** — either a server-side key or paste a key in the web UI (stored only in the browser; sent as `X-Anthropic-Key`)
- **Auth0** application configured for **Resource Owner Password Credentials** (same pattern as the Streamlit app): domain, client id, API audience, realm
- **PostgreSQL** (optional locally) — if `DATABASE_URL` is unset, the API uses **SQLite** next to `orchestrator/store.py` (or `BLUEBOT_CONV_DB` if set)

---

## Local development (recommended)

Run the **API** and the **frontend** in two terminals. The Vite dev server proxies `/api` to the orchestrator on port **8000** (see [`frontend/vite.config.ts`](frontend/vite.config.ts)).

### 1. Configure environment

From the `meter_agent` directory:

```bash
cp .env.example .env
```

Edit `.env` and set at least the **required** variables (see [Environment variables](#environment-variables)). For a first successful login, Auth0 and either `ANTHROPIC_API_KEY` or a key you will paste in the UI are mandatory.

The API loads `.streamlit/secrets.toml` into the environment if present (same keys as Streamlit); otherwise it relies on `.env` or your shell.

### 2. Install and run the orchestrator API

```bash
cd orchestrator
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-api.txt
uvicorn api:app --reload --port 8000 --log-level info
```

Leave this process running. Health check: open `http://127.0.0.1:8000/api/config` (public JSON, no auth).

### 3. Install and run the frontend

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints (usually **http://localhost:5173**). The UI talks to the API through the proxy at `/api`.

### 4. Sign in

Use your Auth0-backed username and password. If login fails with a configuration error, verify `AUTH0_DOMAIN_*`, `AUTH0_CLIENT_ID_*`, and `AUTH0_API_AUDIENCE_*` match your tenant and that ROPC is allowed for that client.

---

## Environment variables

Copy [`.env.example`](.env.example) to `.env` and set values. Below is a concise map; the example file has full comments.

### Required for a typical local run

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for the orchestrator and tools **unless** users paste a key in the app (then optional). |
| `AUTH0_DOMAIN_PROD` (or `_DEV` if you change `BLUEBOT_ENV`) | Auth0 tenant domain, e.g. `https://your-tenant.auth0.com` |
| `AUTH0_CLIENT_ID_PROD` | Auth0 application client id (ROPC-enabled). |
| `AUTH0_API_AUDIENCE_PROD` | API identifier (audience) for bluebot APIs. |
| `BLUEBOT_ENV` | Suffix for Auth0 variable names; default `PROD` → use `*_PROD` vars. |

### Database

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | If set, conversations use PostgreSQL (e.g. `postgresql://user:pass@host:5432/dbname`). If **unset**, SQLite is used locally. |
| `BLUEBOT_CONV_DB` | Optional path to SQLite file when not using Postgres. |

### Optional but common

| Variable | Purpose |
|----------|---------|
| `ORCHESTRATOR_MODEL` | Main chat model (default Haiku). |
| `ORCHESTRATOR_TPM_GUIDE_TOKENS` / `ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET` | Token budget and compression targets (see `.env.example`). |
| `CORS_ORIGINS` | Comma-separated origins allowed for the API (defaults include `http://localhost:5173`). Set if you use another dev port or a deployed UI. |
| `FRONTEND_DIST` | Path to built SPA; production Docker serves `frontend/dist`. Omit in dev (Vite proxies). |
| `PLOTS_DIR` | Where flow-analysis PNGs are stored; important for persistence on cloud hosts (see `.env.example`). |

### Anthropic key: server vs browser

- **Server:** set `ANTHROPIC_API_KEY` in `.env` or the host environment.
- **Browser:** users can open **Claude API key** in the sidebar, paste a key, and save it in **local storage** only; the client sends it as **`X-Anthropic-Key`** on chat requests. If the server has no key and the user does not paste one, chat will fail until one is provided.

---

## Local deployment with Docker

Build and run the same image the repo uses for production-style deployments (single process: FastAPI + static SPA).

From `meter_agent`:

```bash
docker build -t meter-agent .
docker run --rm -p 8080:8080 --env-file .env meter-agent
```

Then open **http://localhost:8080** (the container listens on `PORT`, default **8080**). The app serves the built React app and `/api` from one origin, so CORS is simpler than in Vite dev mode.

Ensure `.env` contains all required secrets and, for Postgres, that the database is reachable from the container.

---

## Sub-agents (subprocesses)

The orchestrator runs **data-processing-agent**, **meter-status-agent**, and **pipe-configuration-agent** as subprocesses. Their code lives beside `orchestrator/` in this repo. Optional per-agent virtualenvs (`.venv` under each agent directory) are used when present; otherwise the current Python interpreter is used.

When you set a user Anthropic key in the UI, the orchestrator forwards it to subprocesses via `ANTHROPIC_API_KEY` for that request so analysis and pipe tools can use the same key as the main model.

---

## Troubleshooting

- **`401` / “x-api-key header is required”** when calling Anthropic directly: export `ANTHROPIC_API_KEY` in the shell or pass the key explicitly; empty env expands to a missing header.
- **Login fails with Auth0 configuration**: confirm `BLUEBOT_ENV` matches the suffix on your `AUTH0_*` variables and that ROPC is enabled for the client.
- **CORS errors** from the browser: add your frontend origin to `CORS_ORIGINS` or use the Vite proxy (`npm run dev` → `/api` → `localhost:8000`).
- **Plots 404 in production**: ensure `PLOTS_DIR` is on persistent storage and, if you scale to multiple replicas, that the instance serving `/api/plots` is the one that wrote the file (see `.env.example`).

---

## License / support

Internal bluebot tooling; deployment details may vary by environment (e.g. Railway). Adjust ports, TLS termination, and secrets per your security policy.
