# Golden turn fixtures

Each JSON file in this directory pins one expected prompt → tool-call behaviour
for a typical user query. Two consumers:

1. `tests/orchestrator/test_golden_turns_schema.py` — CI-friendly structural
   regression. Runs on every commit. No API key required. Confirms every
   fixture is well-formed and references tools that still exist in the
   orchestrator catalog.

2. `scripts/run_golden_turns.py` — manual replay against the real LLM. Run
   before merging prompt or tool changes. Requires `ANTHROPIC_API_KEY`.
   Prints a pass/fail table and exits non-zero on any mismatch.

## Fixture schema

```jsonc
{
  "id": "single_meter_status",
  "description": "Direct-action status query on a single serial.",
  "user": "Is BB8100015261 online?",
  // Ordered expectations over user-visible tool_call events — each entry must
  // be satisfied in order, but intermediate repetitions are tolerated (LLMs
  // retry tool calls). Internal helper reads, such as profile lookups used to
  // prepare a confirmation card, are intentionally ignored by the runner.
  "expected_tool_sequence": [
    {
      "tool": "check_meter_status",
      // All key/value pairs in args_contains must be present in the LLM's
      // tool_input dict. Unrelated keys are ignored.
      "args_contains": {"serial_number": "BB8100015261"},
      // Canned result returned to the LLM by the manual runner so the
      // conversation can reach end_turn. Ignored by the schema test.
      "mock_result": {
        "success": true,
        "online": true,
        "signal_quality": {"label": "good"}
      }
    }
  ],
  // Tools the LLM must NOT call — enforced by the runner.
  "forbidden_tools": ["configure_meter_pipe", "set_transducer_angle_only"],
  // Substrings that must NOT appear in the assistant reply — enforced by the
  // runner. Use for the user-facing-language guardrail regressions.
  "response_must_not_contain": [
    "analyze_flow_data",
    "/Users/",
    "subprocess"
  ],
  // Optional positive substring checks on the assistant's reply.
  "response_must_contain": [],
  // Which prompt versions this fixture is known to pass against. The runner
  // skips a fixture when the active prompt version is not listed.
  "prompt_versions": ["v1"]
}
```

## Adding fixtures

Rule of thumb: cover each tool at least once and each *decision point* the
prompt explicitly enforces (e.g. "compare two meters → compare_meters, not a
profile loop"). 8–12 fixtures is plenty; more makes the manual replay
expensive without much additional signal.
