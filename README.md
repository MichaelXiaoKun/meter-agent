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
| `BLUEBOT_FLOW_HIGH_RES_BASE` | Override for the high-res flow API base URL (optional). |
| `BLUEBOT_MANAGEMENT_BASE` | Override for the management API base URL used by `get_meter_profile` and pipe-configuration (default `https://prod.bluebot.com`). |
| `DISPLAY_TZ` | IANA zone used by the time-range parser **and** as the secondary fallback for the plot x-axes when no meter or browser timezone is resolved. |
| `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S` | Flow analysis: max plausible seconds between consecutive samples when online (default `60`); used to cap gap detection and coverage expectations. |
| `BLUEBOT_GAP_SLACK` | Multiplier on the above for the gap threshold cap (default `1.5` → 90 s with defaults). |
| `BLUEBOT_PLOT_TZ` | IANA zone for plot x-axes when the orchestrator can't resolve one from the meter or the browser. Falls back to `DISPLAY_TZ` then UTC. |

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

### Meter profile & network type (`get_meter_profile`)

The orchestrator also exposes an in-process tool — [`orchestrator/tools/meter_profile.py`](orchestrator/tools/meter_profile.py) — that queries the Bluebot management API for a device record:

```
GET {BLUEBOT_MANAGEMENT_BASE}/management/v1/device?serialNumber=<serial>
Headers: x-admin-query: true, Authorization: Bearer <token>
```

The response is a JSON array; the first element is used. The tool returns:

- a compact **`profile`** subset (`label`, `model`, `commissioned`, `installed`, `deviceTimeZone`, `organization_name`, `device_groups`, etc.), and
- a **`network_type`** classification derived from `networkUniqueIdentifier`:
  - starts with `FF` (case-insensitive) → **`lorawan`** (typical inter-arrival **12–60 s**, bursty)
  - equals the serial number → **`wifi`** (typical inter-arrival **~2 s**)
  - otherwise → **`unknown`**

The classification is a strong prior when interpreting flow-analysis gaps/coverage: LoRaWAN meters legitimately pause up to ~1 min between samples, while Wi-Fi meters should report roughly every 2 s. `analyze_flow_data` accepts the classification directly:

- Optional input field **`network_type`** (`wifi` | `lorawan` | `unknown`). The orchestrator forwards it to the data-processing subprocess as **`BLUEBOT_METER_NETWORK_TYPE`**.
- [`processors/sampling_physics.py`](data-processing-agent/processors/sampling_physics.py) then picks the healthy inter-arrival cap from this hint:
  - `wifi` → **5 s** (covers typical ~2 s cadence plus jitter)
  - `lorawan` / `unknown` / unset → **60 s** (12–60 s bursty cadence)
  - An explicit `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S` in the environment always wins.
- `BLUEBOT_GAP_SLACK` (default `1.5`) is then applied on top to produce the gap cap (e.g. Wi-Fi ≈ 7.5 s, LoRaWAN ≈ 90 s).
- The verified-metrics block in the report echoes the active hint next to the inter-arrival nominal so users can see which cadence prior was used.

The orchestrator system prompt instructs the model to call `get_meter_profile` before `analyze_flow_data` whenever possible and to pass its `network_type` along — this is the recommended flow when the user is troubleshooting coverage, gaps, or connectivity issues.

### Flow data analysis (`data-processing-agent`)

The **analyze_flow_data** tool runs [`data-processing-agent/main.py`](data-processing-agent/main.py) (see [`orchestrator/tools/flow_analysis.py`](orchestrator/tools/flow_analysis.py)). Summary of behavior:

#### Fetching high-res flow data

- Data is requested from the Bluebot **Flow API v2** high-res endpoint (default base URL in [`.env.example`](.env.example): `BLUEBOT_FLOW_HIGH_RES_BASE`).
- Long ranges are split into **hourly chunks** (≤ 3600 s per request).
- If the API returns **HTTP 404** with body `{"message": "no data to export", "statusCode": 404}` for a chunk (e.g. meter offline that hour), that chunk is **skipped** and the rest of the range is still merged — the run does not fail.

#### Ground-truth metrics vs LLM narrative

- Python processors compute **descriptive stats, gaps, zero-flow periods, signal quality, quiet-flow baseline**, plus **flatline** (near-constant flow rate) and **6 h coverage** buckets.
- **`verified_facts_precomputed`** is injected into the analysis prompt so headline numbers are anchored to the same processors the tools call; the model is instructed to **interpret** those facts, not invent statistics.
- The Markdown report ends with a **Verified metrics (code-generated)** section built from `verified_facts` so users can audit numbers even if the narrative drifts.

#### Machine-readable bundle and stderr markers

On success, `main.py` writes **`analysis_<serial>_<start>_<end>.json`** under `data-processing-agent/analyses/` (override with `BLUEBOT_ANALYSES_DIR`; contents are gitignored) and prints a line on **stderr**:

- `__BLUEBOT_ANALYSIS_JSON__` + JSON `{"path": "/absolute/path/to/analysis_....json"}` — the orchestrator exposes this as **`analysis_json_path`** on the tool result when present.
- `__BLUEBOT_PLOT_PATHS__` + JSON array of PNG paths (unchanged).

Programmatic use without subprocess: [`data-processing-agent/interface.py`](data-processing-agent/interface.py) `run()` returns **`analysis_bundle`** (dict) and **`plot_paths`**.

#### Gaps and sampling (network-type aware)

- **Gaps** use positive inter-arrival times only (duplicate timestamps ignored). The threshold blends **median / high percentiles** so variable cadences (e.g. 12–30 s) are not mistaken for missing data, then applies a **hard cap**:  
  `min(adaptive_threshold, max_healthy_inter_arrival × BLUEBOT_GAP_SLACK)`  
  where `max_healthy_inter_arrival` is resolved in this order:
  1. `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S` explicit override, else
  2. the `network_type` passed to `analyze_flow_data` (`wifi` → 5 s, `lorawan`/`unknown` → 60 s), else
  3. 60 s default.
  With default slack (1.5), a Wi-Fi meter caps gaps around **7.5 s** and a LoRaWAN meter around **90 s**.
- **6 h coverage** expected counts use  
  `min(max(median, P75 spacing), max_healthy_inter_arrival)`  
  so nominal density is not inferred slower than that cap. Irregular series use a slightly lower “sparse” ratio threshold.
- The verified-metrics block in the report prints the active `network_type` hint and resolved cap for auditability.

Override caps via environment (see [`.env.example`](.env.example)): `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S`, `BLUEBOT_GAP_SLACK`. The network-type hint is also accepted directly as the `network_type` input of `analyze_flow_data` (and is automatically forwarded by the orchestrator when the model passes it — see the [meter profile tool](#meter-profile--network-type-get_meter_profile)).

#### Baseline-quality guardrails (refusal scaffolding)

[`processors/baseline_quality.py`](data-processing-agent/processors/baseline_quality.py) is the "should we trust a baseline comparison?" evaluator. It is wired into `verified_facts["baseline_quality"]` **before** any baseline pipeline exists so that the default behaviour when baseline questions are asked — with no data, thin data, or post-regime-change data — is an explicit refusal, not a silent hallucinated answer.

Shape:

```json
"baseline_quality": {
  "state": "not_requested" | "no_history" | "insufficient_clean_days"
         | "regime_change_too_recent" | "partial_today_unsuitable" | "reliable",
  "reliable": bool,
  "reasons_refused": ["..."],
  "recommendations": ["..."],
  "n_days_candidate": int, "n_days_used": int, "n_days_rejected": int,
  "days_rejected": [{"local_date": "...", "reason": "..."}],
  "change_point_detected": bool, "change_point_date": str | null, "post_change_days": int | null,
  "fraction_of_day_elapsed": float | null, "today_missing_bucket_ratio": float | null,
  "config_used": { ... }
}
```

Refusal rules (all JSON-serialisable, all overridable via env vars):

| Env var | Default | Rule |
|---|---|---|
| `BLUEBOT_BASELINE_MIN_DAYS` | `5` | Minimum clean reference days after filtering. |
| `BLUEBOT_BASELINE_MIN_WEEKDAY_DAYS` | `3` | Same-weekday days required when `target_weekday` is supplied. |
| `BLUEBOT_BASELINE_MAX_DAY_GAP_RATIO` | `0.15` | Drop reference day whose `coverage_ratio` is worse than this. |
| `BLUEBOT_BASELINE_MAX_DAY_LOWQ_RATIO` | `0.20` | Drop reference day whose `low_quality_ratio` exceeds this. |
| `BLUEBOT_BASELINE_MAD_Z` | `3.5` | Robust-outlier rejection on daily totals (MAD z-score). |
| `BLUEBOT_BASELINE_MIN_DAY_FRACTION` | `0.20` | Suppress projection if local day is < 20% elapsed. |
| `BLUEBOT_BASELINE_MAX_TODAY_MISSING` | `0.25` | Suppress projection if today has > 25% missing hour-buckets. |
| `BLUEBOT_BASELINE_MIN_POST_CHANGE_DAYS` | `7` | Minimum post-change-point days before a baseline is usable. |
| `BLUEBOT_BASELINE_CUSUM_Z` | `2.0` | CUSUM sensitivity (robust standard-deviation units). |

Today, `verified_facts["baseline_quality"]` is always the `not_requested` stub (and stripped from the LLM prompt to save tokens); the full verdict is still in the `analysis_*.json` bundle for audit. The data-processing system prompt already enforces: **if `baseline_quality.reliable=false`, the narrative must relay `state` / `reasons_refused` / `recommendations` verbatim and not synthesize any today-vs-typical claim** — so when the baseline pipeline is plugged in, refusals propagate end-to-end on day one.

#### Local-time filter scaffolding (business-hours / weekend slicing)

Mirrors the baseline-quality pattern: [`processors/mask_by_local_time.py`](data-processing-agent/processors/mask_by_local_time.py) is a pure, deterministic predicate evaluator ready for future use. Its spec accepts `timezone` (IANA), `weekdays` (0=Mon..6=Sun), `hour_ranges` (`[start_hour, end_hour)` local, non-overnight), `exclude_dates` (`YYYY-MM-DD` in that timezone), and `include_sub_ranges` (explicit unix-second intervals). Validation is structural and DST-aware — tests cover both the spring-forward and fall-back transitions in `America/Denver`.

`verified_facts["filter_applied"]` is always populated and has four explicit states:

| `state` | meaning |
|---|---|
| `not_requested` | No filter supplied. Dropped from the LLM prompt to save tokens. |
| `invalid_spec` | Spec failed validation; `validation_errors` list attached. |
| `empty_mask` | Valid spec but zero rows matched — refusal, not a silent no-op. |
| `applied` | Spec valid and at least one row kept; `fraction_kept` + `predicate_used` attached. |

The data-processing system prompt enforces the parallel guardrail to baseline-quality: **when `filter_applied.state ≠ applied`, the narrative must not pretend the analysis was scoped; when `state = applied`, the narrative must cite `fraction_kept` / `predicate_used` so readers know the stats are restricted.** End-to-end wiring (threading a `filters` input through `analyze_flow_data` into the subprocess and calling `apply_filter` on the fetched DataFrame) is the next step; until then, the stub keeps the bundle schema stable.

#### Plot honesty: line breaks at real gaps

`matplotlib` will happily draw a single connected polyline through a multi-hour outage, producing a smooth diagonal segment that visually contradicts the gaps already enumerated in `verified_facts.continuity`. To prevent that, [`processors/plots.py`](data-processing-agent/processors/plots.py) ships two helpers:

- `_series_with_gap_breaks(timestamps, values, cap_seconds=None)` — inserts a `NaN` row between any two consecutive samples whose spacing exceeds the cap. The cap defaults to `max_healthy_inter_arrival_seconds()` so the same network-aware rule that drives gap detection drives the visual break (Wi-Fi: > 5 s; LoRaWAN: > 60 s).
- `_to_datetimes_nan_aware(timestamps)` — vectorised unix-seconds → `datetime64[ns]` with `NaN` mapped to `NaT`. Returned as a tz-naive numpy array (logical UTC) because `matplotlib`'s converter handles `NaT` cleanly there but errors on `NaT` inside tz-aware `Timestamp` arrays.

`_time_series`, `_peaks_annotated`, and `_signal_quality` all use these helpers for the connected line; per-sample scatter overlays (low-quality markers, peak annotations) still use the unbroken arrays so individual points stay at their real coordinates. Tests in [`tests/processors/test_plots.py`](tests/processors/test_plots.py) cover the helper's break-count parity with `continuity.detect_gaps` under the same cap, network-type-aware behaviour, and end-to-end assertions that the rendered `Line2D.get_ydata()` actually contains `NaN` after a simulated outage.

#### Plot timezone: render in the resolved local zone

The plot x-axes used to be hard-coded to UTC, which forced the user to do mental math against a verified-facts report that already speaks in the meter's local clock. That is fixed end-to-end:

1. The orchestrator resolves an IANA zone name when calling `analyze_flow_data` ([`orchestrator/tools/plot_tz.py`](orchestrator/tools/plot_tz.py)) and exports it as `BLUEBOT_PLOT_TZ` to the data-processing-agent subprocess. Precedence (first valid IANA zone wins):

   1. `meter_timezone` — typically the meter's `deviceTimeZone` from `get_meter_profile`.
   2. `display_timezone` — the user's browser timezone.
   3. `BLUEBOT_PLOT_TZ` env var — server-wide override.
   4. `DISPLAY_TZ` env var — fallback shared with the time-range parser.
   5. `"UTC"` — final fallback. The axis label becomes `Time (UTC — meter timezone unknown)` so the absence of local context is visible.

2. The system prompt (rule 11) instructs Claude to call `get_meter_profile` before `analyze_flow_data` and pass `profile.deviceTimeZone` as `meter_timezone` (alongside `network_type`).

3. Inside the data-processing-agent, [`processors/plots.py`](data-processing-agent/processors/plots.py) exposes `resolve_plot_tz` / `describe_plot_tz` and pipes the resolved `tzinfo` into `mdates.DateFormatter` for the time-series, peaks-annotated, and signal-quality plots. The flow-duration curve has no time axis and ignores the setting. Each tz-aware plot result includes a `tz` field reporting the IANA zone it actually rendered in.

Tests in [`tests/tools/test_flow_analysis_tz.py`](tests/tools/test_flow_analysis_tz.py) cover the precedence chain and assert via a patched `subprocess.run` that `BLUEBOT_PLOT_TZ` lands in the env passed to the subprocess. Tests in [`tests/processors/test_plots.py`](tests/processors/test_plots.py) confirm the rendered `DateFormatter` carries the resolved zone (`formatter.tz.key == "America/Denver"` etc.) and that the UTC fallback label is explicitly marked.

---

## Testing

A pytest suite under [`tests/`](tests/) exercises the pure processors and the
orchestrator tool clients with mocked HTTP. No subprocess or real API calls
are made.

```bash
# From meter_agent/ — one-time setup of a shared venv for tests
python -m venv .venv
source .venv/bin/activate
pip install -r data-processing-agent/requirements.txt \
            -r orchestrator/requirements.txt \
            -r requirements-dev.txt

pytest -q                                           # whole suite (~1s)
pytest tests/processors/test_baseline_quality.py    # one module
pytest -k "cusum or outlier"                        # by keyword
pytest -m "not integration"                         # skip integration tests
pytest --cov=processors --cov=tools                 # coverage
```

`tests/conftest.py` adds each sub-agent to `sys.path` and clears
`BLUEBOT_*` / `ANTHROPIC_API_KEY` / `AUTH0_*` / `DATABASE_URL` from the
environment at session start so a developer's local `.env` can never leak
into a test. Pytest config (marker definitions, `pythonpath`) lives in
[`pyproject.toml`](pyproject.toml).

Coverage today:

- `processors/baseline_quality.py` — every refusal state, CUSUM
  change-point ordering, MAD outlier mask, same-weekday gate,
  projection-suitability guards, env-driven configuration.
- `processors/mask_by_local_time.py` — all four states
  (`not_requested`, `invalid_spec`, `empty_mask`, `applied`), structural
  validation of every field, DST correctness across spring-forward and
  fall-back days, `exclude_dates` / `include_sub_ranges` semantics, and
  the `apply_filter` fail-safe on a missing `timestamp` column.
- `processors/sampling_physics.py` — healthy inter-arrival resolution
  precedence (explicit override → network-type hint → 60 s default),
  slack clamping, audit record.
- `processors/continuity.py` — adaptive gap detection, the
  `max_healthy_inter_arrival_seconds` cap, and zero-flow span framing.
- `processors/verified_facts.py` — bundle shape, empty-dataframe
  short-circuit, baseline-quality stub slimming.
- `processors/plots.py` — `_series_with_gap_breaks` parity with the
  cap used by `continuity.detect_gaps`, network-type-aware break
  thresholds, and a render-time assertion that `Line2D.get_ydata()`
  contains `NaN` so an outage cannot be drawn as an interpolated
  diagonal. Also covers the plot-side timezone resolver
  (`resolve_plot_tz` / `describe_plot_tz`) and asserts the rendered
  `DateFormatter.tz.key` matches the resolved IANA zone for each
  tz-aware plot type.
- `tools/plot_tz.py` and `tools/flow_analysis.py` — IANA validation,
  the meter-tz → browser-tz → env → UTC precedence chain, and an
  end-to-end check that `BLUEBOT_PLOT_TZ` is exported in the env
  handed to the data-processing-agent subprocess (with
  `subprocess.run` patched, no real process spawned).
- `tools/meter_profile.py` — network-type classification and the
  management API HTTP contract (mocked with `respx`: 200 / 401 / 404 /
  empty-array responses, admin header + bearer token propagation, base
  URL env override).
- `orchestrator/agent.py` system prompt — rule 12 guardrails against
  leaking internal tool names, env vars, `sub-agent` / `subprocess`
  jargon, or absolute filesystem paths into user-facing replies; also
  pins the "refuse briefly + offer an alternative" contract. Read as
  text (no orchestrator imports) so these tests run in any interpreter.

---

## Troubleshooting

- **`401` / “x-api-key header is required”** when calling Anthropic directly: export `ANTHROPIC_API_KEY` in the shell or pass the key explicitly; empty env expands to a missing header.
- **Login fails with Auth0 configuration**: confirm `BLUEBOT_ENV` matches the suffix on your `AUTH0_*` variables and that ROPC is enabled for the client.
- **CORS errors** from the browser: add your frontend origin to `CORS_ORIGINS` or use the Vite proxy (`npm run dev` → `/api` → `localhost:8000`).
- **Plots 404 in production**: ensure `PLOTS_DIR` is on persistent storage and, if you scale to multiple replicas, that the instance serving `/api/plots` is the one that wrote the file (see `.env.example`).
- **Flow analysis `analysis_json_path` missing**: the path is parsed from the data-processing-agent **stderr** (`__BLUEBOT_ANALYSIS_JSON__`). If the subprocess fails or stderr is swallowed, the JSON file may still exist under `data-processing-agent/` when the run completed locally.

---

## License / support

Internal bluebot tooling; deployment details may vary by environment (e.g. Railway). Adjust ports, TLS termination, and secrets per your security policy.
