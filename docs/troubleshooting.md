# Troubleshooting

Use this page for common local, deployment, auth, and analysis failures.

## Contents

- [Startup and deployment](#startup-and-deployment)
- [Authentication](#authentication)
- [Browser and CORS](#browser-and-cors)
- [Conversation persistence](#conversation-persistence)
- [Chat streaming and status timeline](#chat-streaming-and-status-timeline)
- [Flow analysis and plots](#flow-analysis-and-plots)

<a id="startup-and-deployment"></a>

## Startup and deployment

### `/api/config` returns 502 on Railway

`/api/config` is a lightweight public route, so repeated 502s usually mean FastAPI did not finish startup.

Check:

- Service logs for a crash after `Waiting for application startup`.
- `DATABASE_URL` connectivity if using Postgres.
- `BLUEBOT_CONV_DB` if using SQLite on a mounted volume.
- Whether `BLUEBOT_CONV_DB` points to a directory. Directory paths are accepted, but they resolve to `conversations.db` inside that directory.

For Railway volume-backed SQLite:

```env
BLUEBOT_CONV_DB=/data/conversations.db
```

or:

```env
BLUEBOT_CONV_DB=/var/lib/containers/railwayapp/bind-mounts/.../vol_...
```

See [deployment.md](deployment.md) for the full deployment guide.

### App starts locally but not in Docker

Check:

- Required environment variables are actually passed into the container.
- `PORT` is available and mapped correctly.
- `FRONTEND_DIST` points at a built SPA if you override it.
- Postgres is reachable from inside the container, not only from the host shell.

### Backend feels slow or unresponsive with `--reload`

Reload mode watches the backend tree and restarts Uvicorn after file changes. For diagnostics, prefer:

```bash
./run_backend.sh --mode admin
```

If repeated `/api/config` requests are slow in local development, set `ORCHESTRATOR_LIVE_ANTHROPIC_LIMITS=0` so config responses use registry/env fallback limits instead of live Anthropic response-header probes.

<a id="authentication"></a>

## Authentication

### Anthropic returns `401` or `x-api-key header is required`

Set `ANTHROPIC_API_KEY` in the server environment or paste a browser-local key in the app. If a shell variable expands to an empty string, Anthropic treats it like a missing header.

### Admin login fails with Auth0 configuration

Check:

- `BLUEBOT_ENV` matches the suffix on your `AUTH0_*` variables.
- `AUTH0_DOMAIN_PROD` or the selected environment suffix is correct.
- `AUTH0_CLIENT_ID_PROD` is for an app with Resource Owner Password Credentials enabled.
- `AUTH0_API_AUDIENCE_PROD` matches the API identifier.
- The user exists in the expected connection/realm.

Public sales chat does not require Auth0.

<a id="browser-and-cors"></a>

## Browser and CORS

### CORS errors from the browser

Use the Vite proxy in local development:

```bash
cd frontend
npm run dev
```

If you use a different frontend origin, add it to `CORS_ORIGINS`.

### The frontend opens but API calls fail

Check:

- FastAPI is running on port `8000` during Vite development.
- `frontend/vite.config.ts` still proxies `/api` to the correct backend.
- In Docker/Railway, the built frontend and API should be served from the same origin.

<a id="conversation-persistence"></a>

## Conversation persistence

### Sales conversations disappear after reopening the page

Sales conversation IDs are remembered in browser storage, but the conversation bodies are loaded from the backend store. If the backend database was reset or points to a different SQLite/Postgres instance, the sidebar may not be able to restore old conversations.

Check:

- `BLUEBOT_CONV_DB` is stable across restarts when using SQLite.
- Railway has a mounted volume if you expect SQLite persistence.
- `DATABASE_URL` is not accidentally switching the app from SQLite to another empty Postgres database.

### Status disappears during conversation switch or refresh

Sales status is restored through the public status endpoint and stream polling. If it disappears:

- Confirm the backend is still running the active stream.
- Check browser storage/session storage has not been cleared.
- Check the network panel for `/api/public/sales/conversations/{id}/status`.
- Confirm the conversation was not deleted while the stream was active.

<a id="chat-streaming-and-status-timeline"></a>

## Chat streaming and status timeline

### Message stays on "Sending your message"

The chat POST can succeed before the browser receives the first stream event. In server logs, this usually appears as `POST /api/conversations/{id}/chat` followed by repeated `/api/streams/{stream_id}/poll?cursor=0`.

Check:

- The backend process is still alive and not waiting for a reload restart.
- `ORCHESTRATOR_MAX_CONCURRENT_TURNS` is not saturated by other long turns.
- The frontend is polling the stream id returned by the chat POST.
- Current backend code should emit an early process event before heavy turn work; if cursor stays `0`, inspect server logs around the worker thread startup.

### Timeline stops on a tool row

If the poll cursor advances and then repeats at the same value, the last visible timeline item usually points at the blocking tool. For meter-health questions, `check_meter_status` spawns `meter-status-agent`.

Check:

- `BLUEBOT_RECENT_FLOW_SNAPSHOT_TIMEOUT_SECONDS` controls the preflight recent-flow snapshot timeout; the default is `4`. Timeout/unavailable states should appear in Meter Workspace instead of blocking chat.
- `BLUEBOT_METER_STATUS_AGENT_TIMEOUT_SECONDS` controls the status subprocess wrapper timeout; the default is `30`.
- If `meter-status-agent` emitted `__BLUEBOT_STATUS_JSON__` before timing out, the wrapper returns a deterministic fallback report and `status_data`.
- If no structured status facts were emitted, check Bluebot API credentials/connectivity and the subprocess stderr in the backend terminal.

<a id="flow-analysis-and-plots"></a>

## Flow analysis and plots

### Plots 404 in production

Ensure `PLOTS_DIR` is on persistent storage. If the app scales to multiple replicas, the instance serving `/api/plots` must be able to read the file written by the analysis instance.

### `analysis_json_path` is missing

The path is parsed from the data-processing-agent stderr marker `__BLUEBOT_ANALYSIS_JSON__`. If the subprocess fails or stderr is swallowed, the JSON file may still exist under `data-processing-agent/analyses/` when the run completed locally.

### Flow data looks connected through an outage

The plotting pipeline inserts `NaN` line breaks at real data gaps. If a rendered plot appears to draw through an outage:

- Confirm the code path uses [`../data-processing-agent/processors/plots.py`](../data-processing-agent/processors/plots.py).
- Check the active `network_type`; Wi-Fi and LoRaWAN use different healthy inter-arrival caps.
- Check `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S` and `BLUEBOT_GAP_SLACK` overrides.

### Baseline comparison is refused

That is expected when baseline quality is not reliable. The data agent refuses today-vs-typical claims when there are too few clean days, too much missing data, a recent regime change, or an unsuitable partial day. See [admin-agent.md](admin-agent.md#baseline-and-period-comparisons).
