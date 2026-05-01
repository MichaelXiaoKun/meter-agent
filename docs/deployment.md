# Deployment and Local Development

Use this guide for local setup, environment variables, Docker, Railway, and conversation persistence.

## Contents

- [What you need](#what-you-need)
- [Local development](#local-development)
- [Environment variables](#environment-variables)
- [Docker / Railway deployment](#docker-railway-deployment)
- [Database storage](#database-storage)

<a id="what-you-need"></a>

## What you need

- **Python 3.13+** to match the root [`../Dockerfile`](../Dockerfile).
- **Node.js 20+** and npm for the Vite frontend in development.
- **Anthropic API access** through either a server-side `ANTHROPIC_API_KEY` or a browser-local key pasted in the UI.
- **Auth0** application configured for Resource Owner Password Credentials: domain, client id, API audience, and realm.
- **PostgreSQL** optionally. If `DATABASE_URL` is unset, the API uses SQLite next to `orchestrator/store.py` or at `BLUEBOT_CONV_DB`.

<a id="local-development"></a>

## Local development

The Vite dev server proxies `/api` to the orchestrator on port `8000` through [`../frontend/vite.config.ts`](../frontend/vite.config.ts).

### 1. Configure environment

From the `meter_agent` directory:

```bash
cp .env.example .env
```

Edit `.env` and set at least the required variables below. For a first successful admin login, Auth0 and either `ANTHROPIC_API_KEY` or a browser-provided key are mandatory.

The API loads `.streamlit/secrets.toml` into the environment if present as a legacy local fallback. New local setups should use `.env`; hosted deployments should set environment variables directly.

### 2. Start the local servers

For the usual local setup, start both processes from the `meter_agent` directory:

```bash
./run_project.sh --reload
```

To run the backend and frontend independently, use two terminals:

```bash
./run_backend.sh --reload
```

```bash
./run_frontend.sh
```

The scripts create/install missing local dependencies automatically. Add `--install` to force reinstalling dependencies, or `--sqlite` on `run_backend.sh` / `run_project.sh` to ignore `DATABASE_URL` and use local SQLite storage.

Backend health check: open `http://127.0.0.1:8000/api/config`, which is public JSON and does not require auth.

### 3. Manual command equivalent

If you want to run the same commands by hand, start the orchestrator API:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r orchestrator/requirements-api.txt
cd orchestrator
uvicorn api:app --reload --port 8000 --log-level info
```

Then start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints, usually `http://localhost:5173`. The UI talks to the API through the proxy at `/api`.

### 4. Choose a mode

- For the **Sales assistant**, open `/#/sales` or choose Sales from the entry page. No Auth0 login is required.
- For the **Admin assistant**, choose Admin and sign in with your Auth0-backed username and password.

If admin login fails with a configuration error, verify `AUTH0_DOMAIN_*`, `AUTH0_CLIENT_ID_*`, and `AUTH0_API_AUDIENCE_*` match your tenant and that ROPC is allowed for that client.

<a id="environment-variables"></a>

## Environment variables

Copy [`../.env.example`](../.env.example) to `.env` and set values. The example file has full comments; this table gives the map.

### Required for a typical local run

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for the orchestrator and tools unless users paste a key in the app. |
| `AUTH0_DOMAIN_PROD` or `_DEV` | Auth0 tenant domain, e.g. `https://your-tenant.auth0.com`. |
| `AUTH0_CLIENT_ID_PROD` | Auth0 application client id with ROPC enabled. |
| `AUTH0_API_AUDIENCE_PROD` | API identifier/audience for bluebot APIs. |
| `BLUEBOT_ENV` | Suffix for Auth0 variable names; default `PROD` uses `*_PROD` vars. |

### Database

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | If set, conversations use PostgreSQL, e.g. `postgresql://user:pass@host:5432/dbname`. If unset, SQLite is used locally. |
| `BLUEBOT_CONV_DB` | Optional path to SQLite storage when not using Postgres. This can be either a `.db` file path or a mounted volume directory. Directory values resolve to `conversations.db` inside that directory. |

### Optional but common

| Variable | Purpose |
|----------|---------|
| `ORCHESTRATOR_MODEL` | Main admin chat model. |
| `SALES_AGENT_MODEL` | Sales chat model override. If unset, sales falls back to the orchestrator model/default. |
| `SALES_RESPONSE_VERIFICATION` | Public sales-answer verifier toggle. Defaults to `on`; set `off` only for controlled development. |
| `SALES_RESPONSE_GENERAL_VALIDATION` | Validation mode for general sales replies: `rough` by default, `strong` to force the verifier, or `skip` to skip general validation while still escalating factual claims. |
| `SALES_RESPONSE_VERIFIER_MODEL` | Optional sales verifier model. Weaker overrides are ignored unless explicitly allowed. |
| `SALES_RESPONSE_ALLOW_WEAKER_VERIFIER` | Dev-only escape hatch to allow a weaker verifier override. Defaults to disabled. |
| `SALES_RESPONSE_VERIFICATION_ATTEMPTS` | Maximum verifier/rewrite attempts for a sales answer. Defaults to `3`, capped at `5`. |
| `ORCHESTRATOR_INTENT_ROUTER` | Optional per-turn tool subset for the main model: `off`, `rules`, or `haiku`. |
| `ORCHESTRATOR_TPM_GUIDE_TOKENS` / `ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET` | Token budget and compression targets. |
| `CORS_ORIGINS` | Comma-separated origins allowed for the API. Set this for non-default dev ports or deployed UIs. |
| `FRONTEND_DIST` | Path to built SPA; production Docker serves `frontend/dist`. Omit in dev. |
| `PLOTS_DIR` | Where flow-analysis PNGs are stored; important for persistence on cloud hosts. |
| `BLUEBOT_FLOW_HIGH_RES_BASE` | Override for the high-res flow API base URL. |
| `BLUEBOT_MANAGEMENT_BASE` | Override for the management API base URL used by meter profile and pipe configuration. |
| `DISPLAY_TZ` | IANA zone used by time-range parsing and plot fallback. |
| `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S` | Flow analysis cap for plausible seconds between consecutive online samples. |
| `BLUEBOT_GAP_SLACK` | Multiplier on the healthy inter-arrival cap for gap detection. |
| `BLUEBOT_PLOT_TZ` | IANA zone for plot x-axes when no meter/browser timezone is resolved. |
| `BLUEBOT_DATA_AGENT_MODE` | Flow report renderer: `llm` or `template`. |

The model picker may show a 200k context window, but the chat loop also protects the configured input-token-per-minute guide. For local high-TPM testing, raise `ORCHESTRATOR_TPM_GUIDE_TOKENS` and `ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET` together. Raising only the context/compression target can still pause or fail if the next model call cannot fit in the rolling 60-second TPM window.

### Anthropic key: server vs browser

- **Server:** set `ANTHROPIC_API_KEY` in `.env` or the host environment.
- **Browser:** users can open **Claude API key** in the sidebar, paste a key, and save it in local storage only. The client sends it as `X-Anthropic-Key` on chat requests.
- If the server has no key and the user does not paste one, chat will fail until one is provided.

<a id="docker-railway-deployment"></a>

## Docker / Railway deployment

Build and run the same image the repo uses for production-style deployments, with one process serving FastAPI plus the static SPA.

From `meter_agent`:

```bash
docker build -t meter-agent .
docker run --rm -p 8080:8080 --env-file .env meter-agent
```

Then open `http://localhost:8080`. The container listens on `PORT`, defaulting to `8080`. The app serves the built React app and `/api` from one origin, so CORS is simpler than Vite dev mode.

Ensure `.env` contains all required secrets and, for Postgres, that the database is reachable from the container.

For Railway:

- Deploy from the root [`../Dockerfile`](../Dockerfile).
- Let Railway provide `PORT`; the container command already uses `${PORT:-8080}`.
- For SQLite persistence, mount a Railway volume and set `BLUEBOT_CONV_DB` to either the `.db` file path or the mounted directory.
- If `DATABASE_URL` is set, Postgres is used instead of SQLite.
- If service logs stop at `Waiting for application startup`, check database connectivity/path first. `/api/config` will return 502 until FastAPI startup completes.

<a id="database-storage"></a>

## Database storage

For Railway volume-backed SQLite, leave `DATABASE_URL` unset and point `BLUEBOT_CONV_DB` at a file in the mounted volume:

```env
BLUEBOT_CONV_DB=/data/conversations.db
```

If Railway provides only the mounted directory path, that is also accepted:

```env
BLUEBOT_CONV_DB=/var/lib/containers/railwayapp/bind-mounts/.../vol_...
```

The app creates the parent directory if needed and stores the SQLite database as `conversations.db` when the value is a directory.
