import type { MutableRefObject } from "react";
import type { SSEEvent } from "./types";

/**
 * One line per tool: present-continuous (running) and past (done), kept parallel for alignment.
 * Public for compact labels shared with the activity timeline.
 */
export const TOOL_LIFECYCLE: Record<string, { now: string; done: string }> = {
  resolve_time_range: { now: "Resolving the time range…", done: "Resolved the time range" },
  check_meter_status: { now: "Checking the meter…", done: "Checked the meter" },
  get_meter_profile: { now: "Reading the meter profile…", done: "Read the meter profile" },
  list_meters_for_account: { now: "Listing your meters…", done: "Listed your meters" },
  analyze_flow_data: { now: "Analyzing flow data…", done: "Analyzed the flow data" },
  batch_analyze_flow: { now: "Analyzing flow across meters…", done: "Analyzed flow across meters" },
  compare_periods: { now: "Comparing flow periods…", done: "Compared flow periods" },
  rank_fleet_by_health: { now: "Ranking fleet health…", done: "Ranked fleet health" },
  triage_fleet_for_account: { now: "Triaging account fleet…", done: "Triaged account fleet" },
  configure_meter_pipe: { now: "Preparing configuration review…", done: "Prepared configuration review" },
  set_transducer_angle_only: { now: "Preparing configuration review…", done: "Prepared configuration review" },
  sweep_transducer_angles: { now: "Preparing angle sweep review…", done: "Prepared angle sweep review" },
};

function narrowStr(v: unknown): string {
  if (typeof v !== "string") return "";
  return v.trim();
}

function truncStr(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

function narrowNum(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim()) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

function formatUnixSeconds(v: unknown): string {
  const n = narrowNum(v);
  if (n == null) return "";
  try {
    return new Date(n * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return String(v);
  }
}

function formatWindow(input: Record<string, unknown>): string {
  const start = formatUnixSeconds(input.start);
  const end = formatUnixSeconds(input.end);
  if (start && end) return `${start} to ${end}`;
  return "";
}

function cleanList(values: unknown, maxItems = 3): string {
  if (!Array.isArray(values)) return "";
  const clean = values
    .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    .map((v) => v.trim());
  if (clean.length === 0) return "";
  const head = clean.slice(0, maxItems).join(", ");
  return clean.length > maxItems ? `${head} +${clean.length - maxItems}` : head;
}

export interface TurnActivityDetail {
  label: string;
  value: string;
  tone?: "default" | "success" | "warning" | "danger";
}

function detail(
  label: string,
  value: string | undefined,
  tone: TurnActivityDetail["tone"] = "default"
): TurnActivityDetail[] {
  const v = (value ?? "").trim();
  if (!v) return [];
  return [{ label, value: truncStr(v, 80), tone }];
}

function mergeDetails(
  ...groups: Array<TurnActivityDetail[] | undefined>
): TurnActivityDetail[] {
  const out: TurnActivityDetail[] = [];
  const seen = new Set<string>();
  for (const group of groups) {
    for (const d of group ?? []) {
      const key = `${d.label}:${d.value}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(d);
    }
  }
  return out;
}

function toolInputDetails(
  name: string,
  input?: Record<string, unknown>
): TurnActivityDetail[] {
  if (!input) return [];
  const sn = narrowStr(input.serial_number);
  const email = narrowStr(input.email);
  const range = formatWindow(input);
  const network = narrowStr(input.network_type);
  const meterTz = narrowStr(input.meter_timezone);

  if (name === "resolve_time_range") {
    return detail("Request", truncStr(narrowStr(input.description), 64));
  }
  if (name === "analyze_flow_data") {
    return mergeDetails(
      detail("Meter", sn),
      detail("Window", range),
      detail("Network", network),
      detail("Meter TZ", meterTz)
    );
  }
  if (name === "batch_analyze_flow") {
    return mergeDetails(
      detail("Meters", cleanList(input.serial_numbers)),
      detail("Window", range),
      detail("Network", network)
    );
  }
  if (name === "compare_periods") {
    return mergeDetails(
      detail("Meter", sn),
      detail("Network", network),
      detail("Meter TZ", meterTz)
    );
  }
  if (name === "rank_fleet_by_health") {
    return detail("Meters", cleanList(input.serial_numbers));
  }
  if (name === "triage_fleet_for_account") {
    return detail("Account", email);
  }
  if (name === "check_meter_status" || name === "get_meter_profile") {
    return detail("Meter", sn);
  }
  if (name === "list_meters_for_account") {
    return mergeDetails(
      detail("Account", email),
      detail("Limit", narrowNum(input.limit)?.toString())
    );
  }
  if (name === "configure_meter_pipe") {
    return mergeDetails(
      detail("Meter", sn),
      detail("Material", narrowStr(input.pipe_material)),
      detail("Standard", narrowStr(input.pipe_standard)),
      detail("Size", narrowStr(input.pipe_size)),
      detail("Angle", narrowStr(input.transducer_angle))
    );
  }
  if (name === "set_transducer_angle_only") {
    return mergeDetails(
      detail("Meter", sn),
      detail("Angle", narrowStr(input.transducer_angle))
    );
  }
  if (name === "sweep_transducer_angles") {
    return mergeDetails(
      detail("Meter", sn),
      detail("Angles", cleanList(input.transducer_angles, 8)),
      detail(
        "Final",
        input.apply_best_after_sweep === true ? "set best measured" : "last tested"
      )
    );
  }
  return mergeDetails(detail("Meter", sn), detail("Account", email));
}

function toolResultDetails(
  name: string,
  event: SSEEvent
): TurnActivityDetail[] {
  const plotCount = Array.isArray(event.plot_paths) ? event.plot_paths.length : 0;
  const metersCount = Array.isArray(event.meters) ? event.meters.length : 0;
  const cusum = event.analysis_details?.cusum_drift;
  const cusumSkipped = cusum?.skipped === true;
  const drift = narrowStr(cusum?.drift_detected ?? undefined);
  const alarms =
    typeof cusum?.positive_alarm_count === "number" ||
    typeof cusum?.negative_alarm_count === "number"
      ? `${cusum?.positive_alarm_count ?? 0} up / ${cusum?.negative_alarm_count ?? 0} down`
      : "";
  const adequacy =
    typeof cusum?.actual_points === "number" && typeof cusum?.target_min === "number"
      ? `${cusum.actual_points}/${cusum.target_min} pts`
      : "";
  const gap =
    typeof cusum?.gap_pct === "number"
      ? `${Math.round(cusum.gap_pct * 10) / 10}% gaps`
      : "";
  const adequacyValue = [adequacy, gap].filter(Boolean).join(", ");
  const cusumTone: TurnActivityDetail["tone"] =
    cusumSkipped || cusum?.adequacy_ok === false
      ? "warning"
      : drift && drift !== "none"
        ? "warning"
        : "success";
  const plotTypes = Array.isArray(event.plot_summaries)
    ? event.plot_summaries
        .map((s) => s?.title)
        .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
        .slice(0, 3)
        .join(", ")
    : "";
  return mergeDetails(
    detail("Range", narrowStr(event.display_range)),
    cusum
      ? detail(
          "CUSUM",
          cusumSkipped
            ? `skipped: ${narrowStr(cusum.adequacy_reason) || "data adequacy"}`
            : "ran",
          cusumTone
        )
      : undefined,
    cusum && !cusumSkipped ? detail("Drift", drift || "none", cusumTone) : undefined,
    cusum && !cusumSkipped ? detail("Alarms", alarms) : undefined,
    cusum ? detail("Adequacy", adequacyValue, cusumTone) : undefined,
    detail("Meters", metersCount > 0 ? String(metersCount) : undefined),
    detail("Plots", plotCount > 0 ? String(plotCount) : undefined),
    detail("Plot types", plotTypes),
    detail("Plot TZ", narrowStr(event.plot_timezone)),
    event.deduped === true ? detail("Cache", "reused", "success") : undefined,
    event.report_truncated === true
      ? detail("Report", "truncated", "warning")
      : undefined,
    name === "analyze_flow_data" && event.success === true && narrowStr(event.analysis_json_path)
      ? detail("Bundle", "saved", "success")
      : undefined
  );
}

function fmtCount(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "";
  return Math.round(n).toLocaleString();
}

function fmtGap(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "";
  return `${Math.round(n * 10) / 10}%`;
}

function toolResultExplanation(
  name: string,
  event: SSEEvent
): string | undefined {
  if (name !== "analyze_flow_data" || event.success !== true) return undefined;
  const cusum = event.analysis_details?.cusum_drift;
  if (!cusum) return undefined;

  const actual = fmtCount(cusum.actual_points);
  const target = fmtCount(cusum.target_min);
  const gap = fmtGap(cusum.gap_pct);
  const adequacyBits = [
    actual && target ? `${actual} samples vs ${target} minimum` : "",
    gap ? `${gap} gaps` : "",
  ].filter(Boolean);
  const passedText = adequacyBits.length
    ? `Data check passed: ${adequacyBits.join(", ")}.`
    : "Data check passed.";
  const failedText = adequacyBits.length
    ? `Data check: ${adequacyBits.join(", ")}.`
    : "Data check did not pass.";

  if (cusum.skipped === true || cusum.adequacy_ok === false) {
    const reason = narrowStr(cusum.adequacy_reason) || "not enough usable data";
    return `CUSUM drift check was skipped because ${reason}. ${failedText}`;
  }

  const drift = narrowStr(cusum.drift_detected) || "none";
  const pos = fmtCount(cusum.positive_alarm_count) || "0";
  const neg = fmtCount(cusum.negative_alarm_count) || "0";
  const driftText =
    drift === "none"
      ? "No sustained upward or downward drift was detected."
      : `Detected ${drift} drift.`;
  return `CUSUM checked for sustained drift. ${passedText} ${driftText} Alarm count: ${pos} upward, ${neg} downward.`;
}

/** User-visible line while a tool is running (fallback for unknown tool names from the server). */
export function toolNowLine(
  name: string,
  input?: Record<string, unknown>
): string {
  const t = name.trim() || "tool";
  const inp = input && typeof input === "object" ? input : undefined;
  if (inp) {
    if (t === "resolve_time_range") {
      const d = narrowStr(inp.description);
      if (d) return `Resolving the time range (${truncStr(d, 56)})…`;
    }
    if (t === "check_meter_status") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Checking the meter ${sn}…`;
    }
    if (t === "get_meter_profile") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Reading the meter profile for ${sn}…`;
    }
    if (t === "list_meters_for_account") {
      const em = narrowStr(inp.email);
      if (em) return `Listing meters for ${truncStr(em, 40)}…`;
    }
    if (t === "analyze_flow_data") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Analyzing flow data for meter ${sn}…`;
    }
    if (t === "configure_meter_pipe") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Preparing configuration review for meter ${sn}…`;
    }
    if (t === "set_transducer_angle_only") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Preparing configuration review for meter ${sn}…`;
    }
    if (t === "sweep_transducer_angles") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Preparing angle sweep review for meter ${sn}…`;
    }
  }
  if (TOOL_LIFECYCLE[t]) return TOOL_LIFECYCLE[t].now;
  const n = t.replace(/_/g, " ");
  return n ? `Running ${n}…` : "Running a tool…";
}

export function toolDoneLine(
  name: string,
  ok: boolean,
  opts?: { activity?: string; deduped?: boolean }
): string {
  if (!ok) return "Tool run failed";
  const act = opts?.activity?.trim();
  let base: string;
  if (act) {
    base = act;
  } else {
    const t = name.trim() || "tool";
    if (TOOL_LIFECYCLE[t]) base = TOOL_LIFECYCLE[t].done;
    else base = `Finished ${t.replace(/_/g, " ")}`;
  }
  if (opts?.deduped && ok) {
    return `${base} — reused earlier result`;
  }
  return base;
}

const INTENT_SCOPING_TITLE: Record<string, string> = {
  status: "Scoping: meter & account",
  flow: "Scoping: flow & analysis",
  config: "Scoping: pipe & hardware",
  general: "Scoping: general",
  full: "Full tools (no scoping)",
};

function intentScopingTitle(intent: string | undefined): string {
  if (!intent) return "Scoping the turn";
  return INTENT_SCOPING_TITLE[intent] ?? `Scoping: ${intent}`;
}

/**
 * Normalize long-running tool progress lines that only change elapsed seconds,
 * e.g. "… (4s)" vs "… (8s)" or "… 24s" vs "… 28s", so we replace instead of stacking.
 */
function normProgressStemForMerge(s: string): string {
  const t = s.trim();
  if (/\(\d+s\)\s*$/iu.test(t)) return t.replace(/\(\d+s\)\s*$/iu, "(SEC)");
  if (/\u2026\s*\d+s\s*$/u.test(t)) return t.replace(/\u2026\s*\d+s\s*$/u, "\u2026 SEC");
  return t;
}

function mergeProgressLines(prev: string[], line: string): string[] {
  const last = prev.length > 0 ? prev[prev.length - 1] : "";
  if (last && normProgressStemForMerge(last) === normProgressStemForMerge(line)) {
    return [...prev.slice(0, -1), line];
  }
  return [...prev, line];
}

export interface TurnActivityStep {
  seq: number;
  kind:
  | "connecting"
  | "queued"
  | "intent_route"
  | "thinking"
  | "context"
  | "compressing"
  | "rate_limit_wait"
  | "tool"
  | "stream"
  | "done"
  | "error";
  title: string;
  detail?: string;
  tool?: string;
  /** Set when kind === "tool" */
  phase?: "running" | "waiting_confirmation" | "done";
  ok?: boolean;
  /** Latest tool_use ``input`` (for running titles and replay). */
  toolInput?: Record<string, unknown>;
  /**
   * Sub-agent / long-tool heartbeats: each ``tool_progress`` appends a line so
   * the timeline can show stages between the main tool title and completion.
   */
  progressLines?: string[];
  /** Compact parameter/result facts shown under the stage title. */
  details?: TurnActivityDetail[];
}

/** Shown while the model is thinking, before we swap in ``Thought for …``. */
export const IN_FLIGHT_THINKING_TITLE = "Reasoning";

/** True for the live thinking row (not yet replaced by ``Thought for …``). */
export function isInflightThinkingStep(step: TurnActivityStep): boolean {
  return step.kind === "thinking" && !/^Thought for\b/u.test(step.title ?? "");
}

/**
 * Peel trailing ``done`` / ``error`` off the activity strip whenever there is
 * reply body (live or persisted) so markdown sits directly under
 * ``Generating the reply`` and completion lines render underneath the bubble.
 */
export function splitTurnActivityAroundStreamBody(
  steps: TurnActivityStep[],
  hasStreamBody: boolean
): { above: TurnActivityStep[]; below: TurnActivityStep[] } {
  if (!hasStreamBody) {
    return { above: steps, below: [] };
  }
  const above = [...steps];
  const belowFromEnd: TurnActivityStep[] = [];
  while (above.length > 0) {
    const k = above[above.length - 1]!.kind;
    if (k === "done" || k === "error") {
      belowFromEnd.push(above.pop()!);
    } else {
      break;
    }
  }
  belowFromEnd.reverse();
  return { above, below: belowFromEnd };
}

/**
 * Split at the first ``tool`` row so pre-tool narration (``streamLead``) can sit
 * between “reasoning / context / early stream” and tool + sub-agent work.
 */
export function splitActivityAtFirstTool(
  steps: TurnActivityStep[]
): { beforeTools: TurnActivityStep[]; fromFirstTool: TurnActivityStep[] } {
  const i = steps.findIndex((s) => s.kind === "tool");
  if (i < 0) {
    return { beforeTools: steps, fromFirstTool: [] };
  }
  return {
    beforeTools: steps.slice(0, i),
    fromFirstTool: steps.slice(i),
  };
}

/**
 * After the first SSE event, drop the client-only “connecting” step so the
 * timeline shows server-driven stages.
 */
function withoutConnecting(prev: TurnActivityStep[]): TurnActivityStep[] {
  const first = prev[0];
  if (prev.length === 1 && first != null && first.kind === "connecting") {
    return [];
  }
  return prev.filter(
    (p): p is TurnActivityStep =>
      p != null && typeof (p as TurnActivityStep).kind === "string"
  );
}

/**
 * Append or update steps from one SSE event. Keeps order aligned with seq.
 * Tools fold into a single step per call: present-continuous while running, past tense when done.
 */
export function reduceTurnActivity(
  prev: TurnActivityStep[],
  event: SSEEvent,
  streamOpened: MutableRefObject<boolean>
): TurnActivityStep[] {
  const base = withoutConnecting(prev);
  const seq = typeof event.seq === "number" ? event.seq : base.length;

  const push = (step: Omit<TurnActivityStep, "seq"> & { seq?: number }) =>
    [...base, { ...step, seq: step.seq ?? seq } as TurnActivityStep];

  switch (event.type) {
    case "queued":
      return push({
        kind: "queued",
        title: "In queue",
        detail: event.message ?? "Waiting for another turn to finish",
      });
    case "intent_route": {
      const toolCount = Array.isArray(event.tools) ? event.tools.length : 0;
      return push({
        kind: "intent_route",
        title: "Planning the work",
        detail: intentScopingTitle(event.intent),
        details: mergeDetails(
          detail("Route", event.intent ? intentScopingTitle(event.intent).replace(/^Scoping:\s*/u, "") : undefined),
          detail("Source", narrowStr(event.source)),
          detail("Tools", toolCount > 0 ? String(toolCount) : undefined)
        ),
      });
    }
    case "thinking": {
      // Once reply streaming has started, ignore late/replayed thinking events so
      // "Thought for …" does not regress back to "Reasoning".
      if (streamOpened.current) {
        return base;
      }
      const hasRate =
        typeof event.rate_limit_wait_seconds === "number" && event.rate_limit_wait_seconds > 0;
      const last = base[base.length - 1];
      if (last?.kind === "thinking" && !hasRate) {
        return base;
      }
      if (last?.kind === "thinking" && hasRate) {
        return [
          ...base.slice(0, -1),
          { ...last, seq, title: IN_FLIGHT_THINKING_TITLE, detail: undefined },
        ];
      }
      return push({
        kind: "thinking",
        title: IN_FLIGHT_THINKING_TITLE,
        detail: undefined,
      });
    }
    case "token_usage": {
      const pct = Math.round((event.pct ?? 0) * 100);
      const p0 = withoutConnecting(prev);
      const row: TurnActivityStep = {
        seq,
        kind: "context",
        title: "Input budget usage",
        detail: `About ${pct}% of the full model context is in use`,
        details: mergeDetails(
          detail("Input", event.tokens != null ? `${event.tokens.toLocaleString()} tokens` : undefined),
          detail("Full ctx", `${pct}%`)
        ),
      };
      return [...p0.filter((p) => p.kind !== "context"), row];
    }
    case "compressing":
      return push({ kind: "compressing", title: "Tightening context", detail: undefined });
    case "rate_limit_wait": {
      const current = fmtCount(event.current_tokens);
      const estimated = fmtCount(event.estimated_next_tokens);
      const cap = fmtCount(event.tpm_cap ?? event.tpm_limit);
      const overflow = fmtCount(event.overflow_tokens);
      const waited =
        typeof event.waited_seconds === "number" && Number.isFinite(event.waited_seconds)
          ? `${Math.round(event.waited_seconds)}s`
          : undefined;
      const row: TurnActivityStep = {
        seq,
        kind: "rate_limit_wait",
        title: "Waiting for rate-limit headroom",
        detail:
          event.message ||
          (current && estimated && cap
            ? `${current} used + ${estimated} next exceeds ${cap}/min budget`
            : "Waiting for the rolling 60s input-token window to refresh"),
        details: mergeDetails(
          detail("60s used", current, "warning"),
          detail("Next", estimated, "warning"),
          detail("Budget", cap, "warning"),
          detail("Over", overflow, "warning"),
          detail("Waited", waited)
        ),
      };
      const last = base[base.length - 1];
      if (last?.kind === "rate_limit_wait") {
        return [...base.slice(0, -1), row];
      }
      return [...base, row];
    }
    case "tool_call": {
      const tool = event.tool ?? "";
      const deduped = event.deduped === true;
      // Same-turn retry: drop trailing failed rows for this tool so the timeline does not
      // show "Tool run failed" immediately before a successful second attempt.
      let trimmed = base;
      while (trimmed.length > 0) {
        const last = trimmed[trimmed.length - 1];
        if (
          last?.kind === "tool" &&
          last.tool === tool &&
          last.phase === "done" &&
          last.ok === false
        ) {
          trimmed = trimmed.slice(0, -1);
          continue;
        }
        break;
      }
      const toolInput =
        event.input && typeof event.input === "object"
          ? (event.input as Record<string, unknown>)
          : undefined;
      let title = toolNowLine(tool, toolInput);
      if (deduped) {
        title = `${title.replace(/…\s*$/u, "").trim()} — reusing earlier result…`;
      }
      return [
        ...trimmed,
        {
          seq,
          kind: "tool" as const,
          tool,
          phase: "running" as const,
          title,
          detail: undefined,
          toolInput,
          details: mergeDetails(
            toolInputDetails(tool, toolInput),
            deduped ? detail("Cache", "reusing", "success") : undefined
          ),
        },
      ];
    }
    case "tool_progress": {
      const tool = event.tool ?? "";
      const msg = (event.message ?? "").trim();
      if (!msg) return base;
      const i = base.length;
      for (let k = i - 1; k >= 0; k -= 1) {
        const s = base[k];
        if (s?.kind === "tool" && s.tool === tool && s.phase === "running") {
          const line = msg.length > 200 ? `${msg.slice(0, 197).trimEnd()}…` : msg;
          const nextLines = mergeProgressLines(s.progressLines ?? [], line);
          const short = line.length > 72 ? `${line.slice(0, 69).trimEnd()}…` : line;
          return [
            ...base.slice(0, k),
            {
              ...s,
              seq,
              title: s.title,
              progressLines: nextLines,
              detail: short,
              details: s.details,
            },
          ];
        }
      }
      return base;
    }
    case "tool_result": {
      const tool = event.tool ?? "";
      const ok = event.success ?? false;
      const deduped = event.deduped === true;
      const i = base.length;
      for (let k = i - 1; k >= 0; k -= 1) {
        const s = base[k];
        if (s?.kind === "tool" && s.tool === tool && s.phase === "running") {
          return [
            ...base.slice(0, k),
            {
              ...s,
              seq,
              kind: "tool" as const,
              tool,
              phase: "done" as const,
              title: toolDoneLine(tool, ok, {
                activity:
                  typeof event.tool_activity === "string"
                    ? event.tool_activity
                    : undefined,
                deduped,
              }),
              ok,
              progressLines: s.progressLines,
              toolInput: s.toolInput,
              details: mergeDetails(
                toolInputDetails(tool, s.toolInput),
                toolResultDetails(tool, event)
              ),
              detail: ok
                ? toolResultExplanation(tool, event)
                : event.message
                  ? String(event.message)
                  : s.detail,
            },
          ];
        }
      }
      return push({
        kind: "tool",
        tool,
        phase: "done",
        ok,
        title: toolDoneLine(tool, ok, {
          activity:
            typeof event.tool_activity === "string"
              ? event.tool_activity
              : undefined,
          deduped,
        }),
        ...(!ok && event.message
          ? { detail: String(event.message) }
          : { detail: toolResultExplanation(tool, event) }),
        details: toolResultDetails(tool, event),
      });
    }
    case "config_confirmation_required": {
      const workflow = event.config_workflow;
      const proposed = workflow?.proposed_values;
      const toolInput =
        event.input && typeof event.input === "object"
          ? (event.input as Record<string, unknown>)
          : proposed && typeof proposed === "object"
            ? proposed
            : undefined;
      const row: TurnActivityStep = {
        seq,
        kind: "tool",
        tool: event.tool ?? "configuration",
        phase: "waiting_confirmation",
        ok: true,
        title: "Waiting for your confirmation",
        detail: "No device change has been sent.",
        toolInput,
        details: mergeDetails(
          toolInputDetails(event.tool ?? "", toolInput),
          detail("Action", workflow?.action_id, "warning"),
          detail("Status", "needs confirmation", "warning")
        ),
      };
      for (let k = base.length - 1; k >= 0; k -= 1) {
        const s = base[k];
        if (
          s?.kind === "tool" &&
          s.tool === (event.tool ?? "configuration") &&
          s.phase === "running"
        ) {
          return [...base.slice(0, k), row];
        }
      }
      return [...base, row];
    }
    case "config_confirmation_cancelled": {
      const workflow = event.config_workflow;
      return push({
        kind: "tool",
        tool: event.tool ?? "configuration",
        phase: "done",
        ok: true,
        title: "Cancelled configuration change",
        detail: event.message || "No device change was sent.",
        details: mergeDetails(
          detail("Action", workflow?.action_id, "warning"),
          detail("Status", "cancelled", "warning")
        ),
      });
    }
    case "config_confirmation_superseded": {
      const workflow = event.config_workflow;
      return push({
        kind: "tool",
        tool: event.tool ?? "configuration",
        phase: "done",
        ok: true,
        title: "Replaced configuration review",
        detail: event.message || "No device change was sent.",
        details: mergeDetails(
          detail("Action", workflow?.action_id, "warning"),
          detail("Status", "superseded", "warning")
        ),
      });
    }
    case "text_delta":
    case "text_stream": {
      if (streamOpened.current) {
        return prev;
      }
      streamOpened.current = true;
      return push({ kind: "stream", title: "Generating the reply", detail: undefined });
    }
    case "tool_round_limit": {
      const finalized = applyThinkingElapsed(base, 0.1);
      const lim = typeof event.limit === "number" ? event.limit : 0;
      return [
        ...finalized,
        {
          seq,
          kind: "error",
          title: "Step limit reached",
          detail:
            lim > 0
              ? `This reply stopped after ${lim} assistant steps (safety limit). Send a shorter request or continue in a new message.`
              : "This reply hit the assistant step safety limit. Continue in a new message.",
        },
      ];
    }
    case "error": {
      const finalized = applyThinkingElapsed(base, 0.1);
      return [
        ...finalized,
        {
          seq,
          kind: "error",
          title: "Something went wrong",
          detail: event.error,
        },
      ];
    }
    case "done": {
      const finalized = applyThinkingElapsed(base, 0.1);
      return [
        ...finalized,
        {
          seq,
          kind: "done",
          title: "Complete",
          detail: undefined,
        },
      ];
    }
    default:
      return prev;
  }
}

/**
 * Rebuild timeline steps from persisted event log (``turn_activity`` block).
 * Used when loading conversation history.
 */
export function rebuildStepsFromStoredEvents(
  events: Array<Record<string, unknown>>
): TurnActivityStep[] {
  const ref = { current: false } as { current: boolean };
  let acc: TurnActivityStep[] = [];
  for (const raw of events) {
    if (raw == null || typeof raw !== "object") continue;
    acc = reduceTurnActivity(acc, raw as unknown as SSEEvent, ref);
  }
  return acc;
}

const THOUGHT_FOR_PREFIX = "Thought for ";

export function formatThoughtForSeconds(elapsedSec: number): string {
  if (!Number.isFinite(elapsedSec) || elapsedSec < 0) return IN_FLIGHT_THINKING_TITLE;
  if (elapsedSec < 0.1) {
    return `${THOUGHT_FOR_PREFIX}0.1s`;
  }
  const t =
    elapsedSec < 10
      ? (Math.round(elapsedSec * 10) / 10).toString()
      : String(Math.max(1, Math.round(elapsedSec)));
  return `${THOUGHT_FOR_PREFIX}${t}s`;
}

/**
 * When the first token or tool work arrives, replace the in-flight reasoning row
 * with a ``Thought for Ns`` line (client-measured, ChatGPT-style).
 */
export function applyThinkingElapsed(
  steps: TurnActivityStep[],
  elapsedSec: number
): TurnActivityStep[] {
  if (!Number.isFinite(elapsedSec) || elapsedSec < 0) return steps;
  let i = -1;
  for (let k = steps.length - 1; k >= 0; k -= 1) {
    const s = steps[k];
    if (s && isInflightThinkingStep(s)) {
      i = k;
      break;
    }
  }
  if (i < 0) return steps;
  const current = steps[i]!;
  const title = formatThoughtForSeconds(elapsedSec);
  if (current.title === title) return steps;
  return [...steps.slice(0, i), { ...current, title }, ...steps.slice(i + 1)];
}
