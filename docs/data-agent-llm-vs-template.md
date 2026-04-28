# Data-Agent LLM vs Template Experiment

## Summary

`BLUEBOT_DATA_AGENT_MODE` now supports two report-rendering modes:

- `llm` (default): preserves the existing inner data-processing analyst model.
- `template`: renders deterministic Markdown from `verified_facts` via `data-processing-agent/agent_template.py`.

The template path avoids an inner LLM call and still emits the standard time-series, signal-quality, and diagnostic timeline plots through the existing plotting pipeline.

## Expected Trade-Offs

- Cost: `template` should be lower cost because it skips the inner model call.
- Latency: `template` should be faster for detailed-mode analyses for the same reason.
- Quality: `llm` should remain better at nuanced prose, prioritization, and operator-friendly explanation.
- Safety: `template` is more numerically stable because every line is directly populated from deterministic processor fields.

## Verification To Date

Automated tests cover that the template renderer:

- Produces a Markdown analysis body from `verified_facts`.
- Generates the standard plot side effects expected by the rest of the data-processing pipeline.
- Can be selected through `BLUEBOT_DATA_AGENT_MODE=template` while `llm` remains the default.

The existing golden-turn replay suite requires model access for full answer-quality comparison, so the human quality rating portion remains a follow-up evaluation step rather than a completed benchmark in this local checkpoint.

## Recommended Evaluation

Run the same representative flow-analysis prompts twice:

```bash
BLUEBOT_DATA_AGENT_MODE=llm ./scripts/run_golden_turns.py
BLUEBOT_DATA_AGENT_MODE=template ./scripts/run_golden_turns.py
```

Compare:

- Wall-clock latency per turn.
- Anthropic token usage / cost.
- Whether the final user answer preserves the same key facts.
- Whether recommendations are still useful enough for field operators.

Keep `llm` as the default until template-mode answers pass human review for the common troubleshooting and baseline-comparison cases.
