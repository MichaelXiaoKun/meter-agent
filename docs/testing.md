# Testing

The pytest suite under [`../tests/`](../tests/) exercises pure processors, orchestrator tool clients, prompts, storage behavior, and public sales routes with mocked HTTP. Normal tests do not call real Bluebot APIs or spawn real external subprocess work.

## Contents

- [Setup](#setup)
- [Common commands](#common-commands)
- [Coverage map](#coverage-map)
- [Sales-agent checks](#sales-agent-checks)
- [Frontend checks](#frontend-checks)

<a id="setup"></a>

## Setup

From `meter_agent/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r data-processing-agent/requirements.txt \
            -r orchestrator/requirements.txt \
            -r requirements-dev.txt
```

`tests/conftest.py` adds each sub-agent to `sys.path` and clears `BLUEBOT_*`, `ANTHROPIC_API_KEY`, `AUTH0_*`, and `DATABASE_URL` from the environment at session start so a developer's local `.env` cannot leak into tests.

Pytest config, marker definitions, and `pythonpath` live in [`../pyproject.toml`](../pyproject.toml).

<a id="common-commands"></a>

## Common commands

```bash
pytest -q
pytest tests/processors/test_baseline_quality.py
pytest tests/orchestrator/test_sales_agent.py
pytest -k "cusum or outlier"
pytest -m "not integration"
pytest --cov=processors --cov=tools
```

Frontend:

```bash
cd frontend
npm run build
./node_modules/.bin/tsc -b
```

Use the in-app browser for manual frontend smoke checks after UI changes, especially conversation switching, refresh recovery, share links, and stop/cancel states.

<a id="coverage-map"></a>

## Coverage map

Current backend coverage includes:

- `processors/baseline_quality.py`: refusal states, CUSUM change-point ordering, MAD outlier mask, same-weekday gate, projection-suitability guards, and env-driven configuration.
- `processors/mask_by_local_time.py`: `not_requested`, `invalid_spec`, `empty_mask`, and `applied` states; structural validation; DST correctness; `exclude_dates`; `include_sub_ranges`; fail-safe behavior on missing `timestamp`.
- `processors/sampling_physics.py`: healthy inter-arrival resolution precedence, slack clamping, and audit record.
- `processors/continuity.py`: adaptive gap detection, healthy inter-arrival caps, and zero-flow span framing.
- `processors/verified_facts.py`: bundle shape, empty-dataframe short-circuit, and baseline-quality prompt slimming.
- `processors/plots.py`: line breaks at real gaps, network-type-aware thresholds, render-time `NaN` assertions, plot timezone resolution, and rendered `DateFormatter` timezone checks.
- `tools/plot_tz.py` and `tools/flow_analysis.py`: IANA validation, timezone precedence, and exporting `BLUEBOT_PLOT_TZ` to the data-processing subprocess env.
- `tools/meter_profile.py`: network-type classification and management API HTTP contract with mocked responses.
- `orchestrator/agent.py` prompt tests: user-facing language guardrails that prevent leaking internal tool names, env vars, subprocess details, or absolute file paths.

<a id="sales-agent-checks"></a>

## Sales-agent checks

Sales-agent tests live in [`../tests/orchestrator/test_sales_agent.py`](../tests/orchestrator/test_sales_agent.py).

They cover:

- Sales tool allowlist excludes live status, flow, pipe configuration, and MQTT tools.
- KB retrieval with known product/pipe questions.
- Product-line recommendation output schema.
- Lead qualification and lead-summary persistence.
- Public sales API is unauthenticated and does not expose protected admin data.
- Conversation CRUD.
- Share snapshot creation and revocation.
- Cancel endpoint behavior.
- Status endpoint behavior for stream recovery.
- Railway SQLite volume-directory handling.

<a id="frontend-checks"></a>

## Frontend checks

There is no single frontend e2e suite yet, so use targeted smoke checks when touching the sales/admin chat UI:

- Start a sales message, switch conversations, and confirm the sidebar status remains consistent.
- Refresh while a sales response is running and confirm the status and response recover.
- Confirm the stop button cancels the active sales stream.
- Confirm New chat is stable when the active conversation is empty.
- Delete a conversation and confirm the page does not become blank.
- Create and revoke a sales share link.
- Compare the sales assistant sidebar/status/input behavior against the admin assistant.
