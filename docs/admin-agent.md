# Admin Agent and Tool Deep Dives

The admin assistant is the authenticated support/diagnostics surface. It can call live meter/account tools, invoke specialist sub-agents, analyze flow data, and support pipe-configuration workflows.

## Contents

- [Admin responsibilities](#admin-responsibilities)
- [Sub-agents](#sub-agents)
- [Meter profile and network type](#meter-profile-and-network-type)
- [Flow data analysis](#flow-data-analysis)
- [Baseline and period comparisons](#baseline-and-period-comparisons)
- [Local-time filters](#local-time-filters)
- [Plot honesty and timezone handling](#plot-honesty-and-timezone-handling)
- [Fleet health tools](#fleet-health-tools)

<a id="admin-responsibilities"></a>

## Admin responsibilities

Admin mode owns protected workflows that are deliberately unavailable to public sales chat:

- Live meter/account lookup.
- Flow history analysis.
- Meter status and client health.
- Pipe configuration workflows.
- MQTT-related pipe setup actions.
- Account-level and fleet-level triage.

The admin prompt lives in [`../orchestrator/prompts/system_v1.md`](../orchestrator/prompts/system_v1.md). Routing and tool orchestration live in [`../orchestrator/agent.py`](../orchestrator/agent.py) and [`../orchestrator/tools/`](../orchestrator/tools/).

<a id="sub-agents"></a>

## Sub-agents

The orchestrator runs these specialist subprocesses:

| Agent | Path | Responsibility |
|-------|------|----------------|
| Data processing | [`../data-processing-agent/`](../data-processing-agent/) | Fetch high-res flow data, compute deterministic metrics, create plots, and render reports. |
| Meter status | [`../meter-status-agent/`](../meter-status-agent/) | Check live meter/client state and health-score components. |
| Pipe configuration | [`../pipe-configuration-agent/`](../pipe-configuration-agent/) | Support pipe setup and MQTT-related configuration flows. |

Optional per-agent virtualenvs under each agent directory are used when present. Otherwise the current Python interpreter is used.

When a user sets an Anthropic key in the UI, the orchestrator forwards it to subprocesses via `ANTHROPIC_API_KEY` for that request so analysis and pipe tools can use the same key as the main model.

<a id="meter-profile-and-network-type"></a>

## Meter profile and network type

The in-process [`get_meter_profile`](../orchestrator/tools/meter_profile.py) tool queries the Bluebot management API for a device record:

```text
GET {BLUEBOT_MANAGEMENT_BASE}/management/v1/device?serialNumber=<serial>
Headers: x-admin-query: true, Authorization: Bearer <token>
```

The response is a JSON array; the first element is used. The tool returns:

- a compact `profile` subset such as `label`, `model`, `commissioned`, `installed`, `deviceTimeZone`, `organization_name`, and `device_groups`;
- a `network_type` classification derived from `networkUniqueIdentifier`.

Network-type classification:

| Signal | Classification | Typical cadence |
|--------|----------------|-----------------|
| `networkUniqueIdentifier` starts with `FF` | `lorawan` | 12-60 seconds, bursty |
| `networkUniqueIdentifier` equals the serial number | `wifi` | Around 2 seconds |
| Anything else | `unknown` | Treat conservatively |

The classification is a strong prior when interpreting gaps and coverage. The system prompt instructs the model to call `get_meter_profile` before `analyze_flow_data` whenever possible and to pass both `profile.deviceTimeZone` and `network_type` forward.

<a id="flow-data-analysis"></a>

## Flow data analysis

The `analyze_flow_data` tool runs [`../data-processing-agent/main.py`](../data-processing-agent/main.py) through [`../orchestrator/tools/flow_analysis.py`](../orchestrator/tools/flow_analysis.py).

### Fetching high-res flow data

- Data is requested from the Bluebot Flow API v2 high-res endpoint.
- Long ranges are split into hourly chunks of 3600 seconds or less.
- If the API returns HTTP 404 with `{"message": "no data to export", "statusCode": 404}` for one chunk, that chunk is skipped and the rest of the range is still merged.

### Ground-truth metrics vs LLM narrative

Python processors compute descriptive stats, gaps, zero-flow periods, signal quality, quiet-flow baseline, flatline detection, and 6-hour coverage buckets.

`verified_facts_precomputed` is injected into the analysis prompt so headline numbers come from deterministic processors. The model should interpret those facts, not invent statistics. The Markdown report ends with a code-generated verified-metrics section so users can audit the numbers.

### Machine-readable output

On success, `main.py` writes an `analysis_<serial>_<start>_<end>.json` file under `data-processing-agent/analyses/`, unless `BLUEBOT_ANALYSES_DIR` overrides it.

The subprocess also emits stderr markers:

- `__BLUEBOT_ANALYSIS_JSON__` plus JSON containing the absolute analysis path.
- `__BLUEBOT_PLOT_PATHS__` plus JSON containing plot paths.

Programmatic use without a subprocess goes through [`../data-processing-agent/interface.py`](../data-processing-agent/interface.py), whose `run()` returns `analysis_bundle` and `plot_paths`.

### Gaps and sampling

Gap detection uses positive inter-arrival times only, ignoring duplicate timestamps. The threshold blends median and high-percentile spacing, then applies a hard cap:

```text
min(adaptive_threshold, max_healthy_inter_arrival * BLUEBOT_GAP_SLACK)
```

`max_healthy_inter_arrival` is resolved in this order:

1. Explicit `BLUEBOT_MAX_HEALTHY_INTER_ARRIVAL_S`.
2. `network_type` passed to `analyze_flow_data`: `wifi` uses 5 seconds; `lorawan` and `unknown` use 60 seconds.
3. Default 60 seconds.

With default slack of `1.5`, Wi-Fi gap caps are around 7.5 seconds and LoRaWAN gap caps are around 90 seconds.

6-hour coverage expected counts use `min(max(median, P75 spacing), max_healthy_inter_arrival)` so nominal density is not inferred slower than the cap. Irregular series use a slightly lower sparse ratio threshold.

<a id="baseline-and-period-comparisons"></a>

## Baseline and period comparisons

[`../data-processing-agent/processors/baseline_quality.py`](../data-processing-agent/processors/baseline_quality.py) decides whether a baseline comparison can be trusted. It is wired into `verified_facts["baseline_quality"]` so the default behavior for baseline questions is explicit refusal when the data is not reliable.

Baseline states:

- `not_requested`
- `no_history`
- `insufficient_clean_days`
- `regime_change_too_recent`
- `partial_today_unsuitable`
- `reliable`

Important environment controls:

| Env var | Default | Rule |
|---------|---------|------|
| `BLUEBOT_BASELINE_MIN_DAYS` | `5` | Minimum clean reference days after filtering. |
| `BLUEBOT_BASELINE_MIN_WEEKDAY_DAYS` | `3` | Same-weekday days required when `target_weekday` is supplied. |
| `BLUEBOT_BASELINE_MAX_DAY_GAP_RATIO` | `0.15` | Drop reference days with too much missing coverage. |
| `BLUEBOT_BASELINE_MAX_DAY_LOWQ_RATIO` | `0.20` | Drop reference days with too much low-quality signal. |
| `BLUEBOT_BASELINE_MAD_Z` | `3.5` | Robust outlier rejection on daily totals. |
| `BLUEBOT_BASELINE_MIN_DAY_FRACTION` | `0.20` | Suppress projection if the local day is too early. |
| `BLUEBOT_BASELINE_MAX_TODAY_MISSING` | `0.25` | Suppress projection if today has too many missing hour buckets. |
| `BLUEBOT_BASELINE_MIN_POST_CHANGE_DAYS` | `7` | Minimum post-change-point days before a baseline is usable. |
| `BLUEBOT_BASELINE_CUSUM_Z` | `2.0` | CUSUM sensitivity. |

When `analyze_flow_data` receives a `baseline_window`, the subprocess fetches that reference period, builds meter-local daily rollups, and populates a real verdict. If `baseline_quality.reliable=false`, the narrative must relay `state`, `reasons_refused`, and `recommendations` rather than synthesizing a today-vs-typical claim.

Related deterministic tools:

- [`../data-processing-agent/processors/seasonality.py`](../data-processing-agent/processors/seasonality.py) builds meter-local hour-of-day profiles when enough history is present.
- `compare_periods` runs two normal flow analyses and computes period-B-minus-period-A deltas for volume, mean flow, peak count, gap rate, and low-quality ratio.
- `event_predicates` on `analyze_flow_data` detects threshold windows with a deliberately small predicate language.
- Dense Wi-Fi windows can populate `verified_facts["frequency_domain"]` with dominant non-zero spectral periods from Welch PSD.

<a id="local-time-filters"></a>

## Local-time filters

[`../data-processing-agent/processors/mask_by_local_time.py`](../data-processing-agent/processors/mask_by_local_time.py) evaluates optional local-time filters for business-hours, weekend, explicit date, and sub-range slicing.

The filter spec accepts:

- `timezone` as an IANA zone.
- `weekdays`, where Monday is `0` and Sunday is `6`.
- `hour_ranges` as `[start_hour, end_hour)` local ranges.
- `exclude_dates` as `YYYY-MM-DD`.
- `include_sub_ranges` as explicit unix-second intervals.

`verified_facts["filter_applied"]` has four states:

| State | Meaning |
|-------|---------|
| `not_requested` | No filter supplied. |
| `invalid_spec` | Spec failed validation; validation errors are attached. |
| `empty_mask` | Valid spec, but zero rows matched. |
| `applied` | Valid spec with at least one row kept. |

Invalid specs or valid filters that match zero rows short-circuit before downstream metrics run.

<a id="plot-honesty-and-timezone-handling"></a>

## Plot honesty and timezone handling

[`../data-processing-agent/processors/plots.py`](../data-processing-agent/processors/plots.py) inserts `NaN` breaks into time-series lines when a real data gap occurs. This prevents matplotlib from drawing a misleading diagonal line through a multi-hour outage.

Helpers:

- `_series_with_gap_breaks(timestamps, values, cap_seconds=None)`
- `_to_datetimes_nan_aware(timestamps)`

The time-series, peaks-annotated, and signal-quality plots use these helpers for connected lines. Scatter overlays still use the original arrays so individual points stay at their true coordinates.

Plot timezone is resolved by [`../orchestrator/tools/plot_tz.py`](../orchestrator/tools/plot_tz.py) and exported as `BLUEBOT_PLOT_TZ` to the data-processing subprocess.

Precedence:

1. `meter_timezone`, typically `profile.deviceTimeZone`.
2. `display_timezone`, the user's browser timezone.
3. `BLUEBOT_PLOT_TZ`.
4. `DISPLAY_TZ`.
5. `UTC`.

If the final fallback is UTC, the axis label makes the missing meter timezone visible.

<a id="fleet-health-tools"></a>

## Fleet health tools

`check_meter_status` includes a composite `health_score` in `status_data`. Staleness and signal quality come from the current status payload, while optional flow-analysis facts can add gap-density and drift components.

`compare_meters` surfaces score and verdict per meter for side-by-side triage.

`rank_fleet_by_health` accepts supplied serial-number lists, fans out status/profile reads for up to 50 meters, and returns a compact table sorted from lowest health score to highest. When a `flow_window` is provided, it also runs summary flow analysis per meter and feeds the resulting `verified_facts` into the same health-score calculation.

`triage_fleet_for_account` starts from a user email, reuses the account meter listing, caps fan-out at 50 meters, and returns the same lowest-health-first table. This is the preferred path for "which meters need attention on this account?" because it avoids model-side serial loops.
