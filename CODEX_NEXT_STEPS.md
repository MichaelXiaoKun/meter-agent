# Bluebot meter agent — Codex implementation plan

This document hands off the next chunk of work after task #1
(*Wire baseline_quality end-to-end through `analyze_flow_data`*) shipped.
It is written so a Codex (or any other) agent can pick up without re-deriving
context from the conversation history.

> **Read first:** [`README.md`](README.md) for the architecture overview, and
> [`orchestrator/prompts/system_v1.md`](orchestrator/prompts/system_v1.md) for
> the orchestrator's behavioural rules. Rules referenced below (e.g. "rule 11",
> "rule 16") are the numbered rules in `system_v1.md`.

---

## 1. Starting state (what task #1 produced)

Already merged on the working tree:

* **`data-processing-agent/processors/daily_rollup.py`** — pure module that
  groups a flow dataframe into local-tz `DailyRollup` dicts (the shape
  `baseline_quality` already expected). Includes DST correctness, partial-today
  rollup with elapsed-fraction sizing, `fraction_of_day_elapsed` helper, and
  `today_missing_bucket_ratio` helper.
* **`data-processing-agent/processors/baseline_compare.py`** — pure module
  that produces the `today_vs_baseline` block (verdict ∈ `typical` /
  `elevated` / `below_normal` / `indeterminate`, projection metrics, robust
  z-score, env-tunable thresholds).
* **`processors/verified_facts.py`** — `build_verified_facts(...)` now takes
  optional `reference_rollups` / `today_partial` / `target_weekday` /
  `fraction_of_day_elapsed` / `today_missing_bucket_ratio`. When supplied it
  runs the real `evaluate_baseline_quality` and (only when `reliable=True`)
  computes `today_vs_baseline`. The slimmer keeps refusal states verbatim.
* **`data-processing-agent/main.py`** — accepts `--baseline-start` /
  `--baseline-end`; fetches reference flow data, builds rollups, threads them
  into `build_verified_facts`. Reuses `BLUEBOT_PLOT_TZ` for day grouping.
* **`data-processing-agent/interface.py`** — `run()` accepts a
  `baseline_window={"start","end"}` kwarg.
* **`orchestrator/tools/flow_analysis.py`** — TOOL_DEFINITION input schema
  has `baseline_window` (oneOf: semantic enum string, or `{start, end}`
  object). `resolve_baseline_window()` translates semantic keys to Unix bounds
  anchored at `primary_start - 1`. Cache key includes the resolved bounds.
  Subprocess CLI gets `--baseline-start` / `--baseline-end`.
* **`orchestrator/agent.py`** — `analyze_flow_data` dispatch passes
  `baseline_window=inputs.get("baseline_window")` through to the function and
  to the worker thread that wraps it for SSE heartbeats.
* **`orchestrator/prompts/system_v1.md` rule 16** — teaches the model when
  to pass `baseline_window`, the semantic-key dictionary
  ("vs last week" → `trailing_7_days`, etc.), and the behaviour split between
  `reliable` (lead with verdict) and refusal states (relay reasons verbatim).
* **Tests:** `tests/processors/test_daily_rollup.py` (17),
  `tests/processors/test_baseline_compare.py` (14),
  `tests/tools/test_flow_analysis_baseline.py` (17). All 307 tests in the
  pure-processor + tool-wiring + system-prompt suites pass.

The same shape of work — pure processor → `verified_facts` integration →
subprocess fetch/wire → orchestrator tool input → system prompt rule → tests —
is the **template** for almost every task below.

---

## 2. Shared conventions (do not reinvent)

These emerged in #1 and should be reused so the codebase stays coherent.

### 2.1 Refusal scaffolding pattern

Every "feature that the model can ask for and we may not be able to deliver"
follows the **state-string pattern** modelled after `baseline_quality`:

```
{
  "state": "not_requested" | <refusal_state_1> | <refusal_state_2> | ... | "<success_state>",
  "reliable" / "applied" / etc.: bool,
  "reasons_refused": [str, ...],
  "recommendations": [str, ...],
  "<provenance fields>": ...,
  "config_used": {...},
}
```

Reference implementations:
[`processors/baseline_quality.py`](data-processing-agent/processors/baseline_quality.py),
[`processors/mask_by_local_time.py`](data-processing-agent/processors/mask_by_local_time.py).

When you wire a feature end-to-end:

1. **Always populate** the state field — never `null` it. `not_requested` is a
   first-class state.
2. The **system prompt rule** must require relaying refusal `reasons_refused`
   verbatim and offering a concrete alternative (rule 15 already covers
   "user-facing language"; the feature-specific rule should explicitly say
   "do NOT synthesise around the refusal").
3. **`slim_verified_facts_for_prompt`** drops only the `not_requested` stub
   from the LLM prompt. Refusals AND success states stay in.

### 2.2 Env-driven configuration

* All thresholds and tunables live in a `Config` dataclass with a
  `from_env()` classmethod (see `BaselineQualityConfig`).
* Env-var names are prefixed `BLUEBOT_<FEATURE>_<KNOB>`.
* Don't bury heuristics in the code — name them, document the default in the
  README's "Environment variables" section, and make them overridable.

### 2.3 Subprocess plumbing

* The orchestrator tool wrapper passes inputs to the subprocess via either
  CLI args (preferred for typed integers) or env vars (preferred for opaque
  strings like timezones, network types, semantic keys that the subprocess
  doesn't need to parse beyond "is it set?").
* The data-processing subprocess emits machine-readable artefacts via stderr
  markers: `__BLUEBOT_ANALYSIS_JSON__`, `__BLUEBOT_PLOT_PATHS__`,
  `__BLUEBOT_REASONING_SCHEMA__`, etc. Add a new marker if you have a new
  multi-line structured payload.
* Tests for tool wiring use the **subprocess-stub pattern** — see
  [`tests/tools/test_flow_analysis_baseline.py`](tests/tools/test_flow_analysis_baseline.py)
  and `test_flow_analysis_tz.py`. They patch `subprocess.run` and stub
  `processors.time_range` / `subprocess_env` to dodge the
  orchestrator-vs-data-processing-agent `processors` namespace collision on
  the shared test sys.path.

### 2.4 Cache invalidation

`tools/flow_analysis.py::_RESULT_CACHE` is keyed on every input that affects
the subprocess output. **When you add an input that changes the result, add it
to the cache key.** The baseline_window addition appended a `tuple[int, int] |
None` to the key tuple — follow the same pattern.

### 2.5 System prompt numbering

`tests/orchestrator/test_system_prompt.py` walks `1..N` and requires every
rule number to exist. **Append new rules at the bottom**, do NOT insert in
the middle, do NOT skip numbers. Rule 15 ("User-facing language") is the
keystone — its content is checked by name, not number.

### 2.6 Test commands

```bash
# fast pure-processor + tool-wiring sweep (this is what CI should run by default)
python3 -m pytest -q --no-header \
  tests/processors/ tests/tools/ tests/orchestrator/test_system_prompt.py

# single file
python3 -m pytest tests/processors/test_<name>.py -q

# orchestrator API tests need fastapi + sqlalchemy + python-dotenv installed
```

Sandbox note: `tests/tools/test_meter_profile.py` fails in some sandboxes due
to httpx + SOCKS proxy. That is a pre-existing environmental issue, not a
real regression — confirm by running on host if you suspect a real break.

---

## 3. Immediate next pickup — Task #2

**Task #2: Wire `mask_by_local_time` filters end-to-end through
`analyze_flow_data`.**

This is structurally a clone of #1. The scaffolding is already there:

* [`processors/mask_by_local_time.py`](data-processing-agent/processors/mask_by_local_time.py)
  — pure validator + applier (`apply_filter`).
* `verified_facts["filter_applied"]` is already populated with one of four
  states: `not_requested` / `invalid_spec` / `empty_mask` / `applied`.
* The data-processing system prompt already enforces refusal propagation
  (see `data-processing-agent/agent.py`'s system prompt — search for
  `filter_applied`).
* Rule 15(c) in `system_v1.md` already gives an example user-facing refusal
  for "filter to business hours."

What's missing is the actual wiring — `filters` doesn't reach the subprocess
yet. **Definition of done:**

1. **`analyze_flow_data` TOOL_DEFINITION** in
   `orchestrator/tools/flow_analysis.py` gets a new optional input
   `filters: object` with the same JSON shape `apply_filter` expects:
   ```json
   {
     "timezone": "America/Denver",
     "weekdays": [0, 1, 2, 3, 4],
     "hour_ranges": [[8, 17]],
     "exclude_dates": ["2026-12-25"],
     "include_sub_ranges": [[1700000000, 1700050000]]
   }
   ```
   Document the field semantics in the description (DST-aware,
   non-overnight, 0=Mon).

2. **Resolver** — add a `resolve_filters(spec, *, primary_start, primary_end)`
   helper next to `resolve_baseline_window`. It can be a pass-through (the
   subprocess does the validation), but it should return `None` for the
   missing/empty case so the cache key stays compact.

3. **Cache key** extension — append the canonicalised filters dict (or
   `None`) to the `_RESULT_CACHE` key. Use `json.dumps(filters,
   sort_keys=True)` as the canonical form.

4. **Subprocess wiring** — pass the filters to
   `data-processing-agent/main.py` as a single JSON-serialised env var
   `BLUEBOT_FILTERS_JSON` (CLI args are awkward for nested objects).
   `main.py` reads it, passes to `build_verified_facts(...,
   filters=parsed)`. *Add the new kwarg to `build_verified_facts` and call
   `apply_filter(df, parsed)` BEFORE the rest of the pipeline runs* — this
   way every other processor sees the already-filtered series, and
   `verified_facts["filter_applied"]` echoes the state.

   Important: if `state != "applied"` (refusal or empty mask), do NOT run
   the rest of the pipeline against an empty / unfiltered series — short-
   circuit and let `verified_facts` carry only the refusal block.

5. **`interface.py`** — accept `filters` kwarg on `run()`, mirror the same
   path.

6. **System prompt rule 17** — append (do NOT insert before existing rules).
   Header: "Local-time filtering". Cover when to pass `filters` (user said
   "weekdays only", "business hours", "exclude holidays"), how to map common
   phrasings to the JSON shape, and the behaviour split: `state == "applied"`
   means cite `fraction_kept` / `predicate_used`; `state in {"invalid_spec",
   "empty_mask"}` means relay the refusal verbatim and offer an alternative.

7. **Tests** mirroring #1:
   * `tests/processors/test_mask_by_local_time.py` already exists — extend
     it if you change the public API. **Don't change the public API** if you
     can avoid it; the system prompt assumes the existing four states.
   * New `tests/tools/test_flow_analysis_filters.py`: subprocess-stub
     pattern, asserts `BLUEBOT_FILTERS_JSON` lands in the env, asserts cache
     key separates by filters, asserts a malformed filters value is silently
     dropped (no crash).
   * Extend `tests/processors/test_verified_facts.py` to confirm that
     supplying `filters` produces `filter_applied.state == "applied"` and
     that downstream metrics reflect the filtered subset.

Estimated touched files: ~6, ~250 lines net.

---

## 4. Subsequent tasks — outlines

These are smaller. Each follows the same template (pure processor → wiring →
optional system-prompt rule → tests). Spec-level details only — Codex should
fill in shape decisions as they would for #1/#2.

### Task #3 — Diurnal/weekly seasonality processor

* New `processors/seasonality.py`. Two callables:
  * `build_diurnal_profile(df, *, tz, n_days=28)` → `{"hour": {0..23: median_flow_rate, ...}, "p25": {...}, "p75": {...}, "n_days_used": int}`.
  * `score_against_diurnal(today_df, profile)` → per-hour z-score plus a single
    "departure_score" summary (max-abs hourly z over the elapsed hours).
* Inputs: same flow dataframe shape as everything else; tz from
  `BLUEBOT_PLOT_TZ`.
* No new tool input — this lives **inside** `verified_facts` and is auto-run
  whenever a baseline window is supplied (it's a useful free byproduct of
  reference data we already fetched).
* Refusal: when `n_days_used < 7` skip and emit `state: "insufficient_history"`.
* Tests: pure unit tests; check DST behavior; check the score on synthetic
  data with a known hour-7 spike.

### Task #4 — Period-over-period comparison tool

Depends on #1 (✓ shipped). Could be a new top-level tool OR a new shape of
`analyze_flow_data` (`baseline_window` already supports explicit
`{start,end}`). **Recommendation: NEW tool** so the model isn't tempted to
overload `analyze_flow_data` for fundamentally different output shapes.

* New `orchestrator/tools/period_compare.py` (`compare_periods` tool).
* Inputs: `serial_number`, two windows `period_a` / `period_b`
  (`{start,end}`), optional `network_type`, optional `meter_timezone`.
* Calls `analyze_flow_data` twice in parallel (reuse the existing function;
  no subprocess plumbing needed) and emits a deltas block:
  `{"volume_delta_gallons": float, "volume_delta_pct": float,
  "mean_flow_delta": float, "peak_count_delta": int,
  "gap_rate_delta": float, "low_quality_ratio_delta": float}`.
* No system prompt rule needed — tool description carries the contract.
* Tests: parallel-call shape assertion, deltas correctness on synthetic
  fixtures.

### Task #5 — Cross-meter correlation / fleet rank tool

Depends on `batch_analyze_flow` (already exists) and #6 (composite health
score, below).

* New tool `rank_fleet_by_health` that fans out to `check_meter_status` +
  `get_meter_profile` for each serial, computes the composite health score
  (#6), and returns a ranked list.
* Optionally include a "correlation matrix" mode for flow-rate cross-
  correlation when caller supplies a time window.
* Output bounded to ≤ 50 meters.

### Task #6 — Composite meter health score

Smallest standalone task. **Recommended warmup if you want a quick win.**

* New `processors/health_score.py`:
  * `compute_health_score(*, status, profile, verified_facts) -> {"score": 0-100, "components": {...}, "verdict": "healthy"|"degraded"|"unhealthy"}`.
  * Weights: staleness 40% / signal quality 30% / gap density 20% / drift 10%.
  * All inputs already exist in current tool outputs.
* Surface on `check_meter_status` result (additive field); make it available
  to `compare_meters` for the per-meter row.

### Task #7 — Threshold/event detector processor

Depends on #2 (filters wiring).

* New `processors/event_detector.py`:
  `detect_threshold_events(df, *, predicate, min_duration_seconds)` →
  list of `{"start_ts","end_ts","duration_seconds","peak_value"}`.
* Predicate is a small expression language: `"flow > 10"`, `"flow == 0"`,
  `"quality < 60"`. Keep this thin — no full DSL.
* Add as a new optional input on `analyze_flow_data`:
  `event_predicates: list[{"name":str, "predicate":str, "min_duration_seconds":int}]`.
  Refusal: malformed predicate → `state: "invalid_predicate"`.
* No system prompt rule needed if the model doesn't proactively use it; add a
  rule if you want the assistant to surface events automatically when the
  user describes a threshold question.

### Task #8 — Frequency-domain (PSD/FFT) probe

* New `processors/frequency_domain.py`: `compute_dominant_frequencies(timestamps, values, *, top_k=3)` → list of `{"frequency_hz","amplitude","period_seconds"}`.
* Use scipy's PSD; resample to a fixed cadence first (it's not safe to FFT
  irregular series).
* Surface inside `verified_facts` only when the analysis window is ≥ 1 hour
  AND the meter is Wi-Fi cadence (LoRaWAN data is too sparse for
  frequency-domain analysis).
* Refusal: `state: "insufficient_cadence"` when the prerequisites aren't met.

### Task #9 — A/B test removing the inner LLM in `data-processing-agent`

Not a build task — an experiment. Steps:

1. Build a `data-processing-agent/agent_template.py` that produces the same
   Markdown report shape as `agent.analyze` but using a **fixed string
   template** populated from `verified_facts` (no LLM call).
2. Add an env switch: `BLUEBOT_DATA_AGENT_MODE = "llm" | "template"` (default
   `llm` to keep current behaviour).
3. Run the existing golden-turn suite under both modes; compare on cost,
   latency, and analyst-rated quality.
4. Document findings in `docs/data-agent-llm-vs-template.md`. **Deliverable
   is the report**, not necessarily the kill of the inner LLM.

### Task #10 — Fleet-triage CUJ on top of `list_meters_for_account`

Depends on #6.

* New tool `triage_fleet_for_account` that takes an email, calls
  `list_meters_for_account` internally, then fans out parallel
  `check_meter_status` + `get_meter_profile` calls (cap at 50 meters), and
  returns a compact triage table:
  `[{serial, status, signal, last_seen_age_seconds, health_score, top_concern}]`.
* System prompt rule 18 (append): when user asks "which meters need
  attention?" / "how is everything looking?" — call `triage_fleet_for_account`
  rather than letting the model loop the per-serial tools.

---

## 5. Strategic / non-build tasks (#11–#15)

These are larger or more architectural. **Do not start without an explicit
green-light** — they are refactors / decisions, not features.

### Task #11 — Refactor `orchestrator/agent.py` (2.2k lines)

Pull these concerns into separate modules:

* `tool_dedupe.py` (read-cache key, write-tool serialization).
* `intent_routing.py` (the rules + Haiku classifier).
* `compression.py` (the summarization passes for long threads).
* `sse_events.py` (event shaping for the SSE stream).
* `tool_dispatch.py` (the giant `if/elif` chain in `_dispatch_one`).

Existing tests
(`test_tool_dedupe`, `test_tool_loop_guards`, `test_intent_routing`,
`test_observability`) should pass unchanged. **No behaviour change.**

### Task #12 — Per-intent system-prompt slices

Depends on #11.

Today `ORCHESTRATOR_INTENT_ROUTER` trims the tool set per turn but the main
model still sees the full `system_v1.md`. Extend it so each intent has its
own slice (e.g. `intents/flow.md` includes rules 1, 2, 3, 11, 12, 16; the
router selects which slices to concatenate). The full prompt is the union;
per-intent prompts are subsets.

### Task #13 — Declarative sub-agent registry

Depends on #11.

Replace the per-tool subprocess wrappers (`flow_analysis.py`,
`pipe_configuration.py`, etc.) with a single `subagents.py` registry:

```python
SUBAGENTS = {
  "flow_analysis": SubagentSpec(
    cwd="data-processing-agent",
    cli=["main.py"],
    env_passthrough=["BLUEBOT_PLOT_TZ", "BLUEBOT_METER_NETWORK_TYPE", ...],
    stderr_markers=["__BLUEBOT_ANALYSIS_JSON__", ...],
  ),
  ...
}
```

Each tool wrapper becomes a thin adapter that reads the spec.

### Task #14 — Defer meta-orchestrator

Decision document, not code. Write `docs/architecture/meta-orchestrator-deferral.md`
explaining why we are not adding another layer above the orchestrator
(intent routing already covers the planning win, tool count is below the
planner-strain threshold, all workflows live in one synchronous mode).
Revisit when #15 ships.

### Task #15 — Persistent monitoring agent (post-baseline)

Depends on #1 (✓ shipped). Design doc first; build only after sign-off.

Deliverable: `docs/architecture/monitoring-agent.md` covering:

* Background-worker shape (Celery? Postgres-backed scheduler? Cron?).
* Notification routing (email? Slack? Webhook?).
* Persistent state (which meters subscribed, last-alert timestamps, learned
  thresholds).
* The sync-chat-agent ↔ async-monitor-agent boundary — this is also the
  trigger for revisiting #14.

---

## 6. Hand-off checklist for Codex

Before declaring any task done:

- [ ] All new processors are pure (no I/O); side effects live in
      `main.py` / `interface.py` / tool wrappers.
- [ ] Refusal scaffolding pattern followed: `state` field is exhaustive,
      `not_requested` is a state.
- [ ] `slim_verified_facts_for_prompt` updated if the new field needs
      different prompt-vs-bundle treatment.
- [ ] Cache key in `_RESULT_CACHE` extended for any new input affecting
      output.
- [ ] System prompt rule appended (not inserted) if the model needs to know
      about the feature.
- [ ] Tests added: pure unit tests for the processor; tool-wiring tests
      using the subprocess-stub pattern.
- [ ] Existing test suite still green:
      `python3 -m pytest -q tests/processors/ tests/tools/test_flow_analysis_tz.py
      tests/tools/test_flow_analysis_baseline.py
      tests/orchestrator/test_system_prompt.py`
- [ ] README's "Environment variables" section updated for any new
      `BLUEBOT_*` env var.
- [ ] CHANGELOG / commit message names the rule number you added (so
      `system_v1.md` history is traceable).

---

## 7. Suggested execution order

1. **#2** (filters wiring) — unblocks #7, mirrors #1 exactly.
2. **#6** (health score) — small, unblocks #5 and #10.
3. **#3** (seasonality) — slots inside `verified_facts` next to baseline.
4. **#4** (period-over-period) — first new tool of the batch.
5. **#5** (fleet rank) — depends on #6.
6. **#10** (fleet triage CUJ) — depends on #5 + #6.
7. **#7** (event detector) — depends on #2.
8. **#8** (PSD/FFT) — independent, low priority.
9. **#11** → **#12** → **#13** — refactor batch; gate on a quiet sprint.
10. **#9** (A/B test inner LLM) — experiment; can run any time.
11. **#14** / **#15** — design docs; gate on management decision.

Total of #2–#10 is roughly 1.5–2 weeks of focused work for one engineer at
the pace task #1 set.
