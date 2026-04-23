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
  configure_meter_pipe: { now: "Configuring the pipe…", done: "Configured the pipe" },
  set_transducer_angle_only: { now: "Setting the transducer angle…", done: "Set the transducer angle" },
};

function narrowStr(v: unknown): string {
  if (typeof v !== "string") return "";
  return v.trim();
}

function truncStr(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
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
      if (sn) return `Configuring the pipe for meter ${sn}…`;
    }
    if (t === "set_transducer_angle_only") {
      const sn = narrowStr(inp.serial_number);
      if (sn) return `Setting the transducer angle for meter ${sn}…`;
    }
  }
  if (TOOL_LIFECYCLE[t]) return TOOL_LIFECYCLE[t].now;
  const n = t.replace(/_/g, " ");
  return n ? `Running ${n}…` : "Running a tool…";
}

export function toolDoneLine(
  name: string,
  ok: boolean,
  opts?: { activity?: string }
): string {
  if (!ok) return "Tool run failed";
  const act = opts?.activity?.trim();
  if (act) return act;
  const t = name.trim() || "tool";
  if (TOOL_LIFECYCLE[t]) return TOOL_LIFECYCLE[t].done;
  return `Finished ${t.replace(/_/g, " ")}`;
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
  | "tool"
  | "stream"
  | "done"
  | "error";
  title: string;
  detail?: string;
  tool?: string;
  /** Set when kind === "tool" */
  phase?: "running" | "done";
  ok?: boolean;
  /** Latest tool_use ``input`` (for running titles and replay). */
  toolInput?: Record<string, unknown>;
  /**
   * Sub-agent / long-tool heartbeats: each ``tool_progress`` appends a line so
   * the timeline can show stages between the main tool title and completion.
   */
  progressLines?: string[];
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
      return push({
        kind: "intent_route",
        title: intentScopingTitle(event.intent),
        detail: undefined,
      });
    }
    case "thinking": {
      const hasRate =
        typeof event.rate_limit_wait_seconds === "number" && event.rate_limit_wait_seconds > 0;
      const last = base[base.length - 1];
      if (last?.kind === "thinking" && !hasRate) {
        return base;
      }
      if (last?.kind === "thinking" && hasRate) {
        return [
          ...base.slice(0, -1),
          { ...last, seq, title: "Thinking", detail: undefined },
        ];
      }
      return push({ kind: "thinking", title: "Thinking", detail: undefined });
    }
    case "token_usage": {
      const pct = Math.round((event.pct ?? 0) * 100);
      const p0 = withoutConnecting(prev);
      const row: TurnActivityStep = {
        seq,
        kind: "context",
        title: "Context",
        detail: `About ${pct}% of the model window in use`,
      };
      return [...p0.filter((p) => p.kind !== "context"), row];
    }
    case "compressing":
      return push({ kind: "compressing", title: "Tightening context", detail: undefined });
    case "tool_call": {
      const tool = event.tool ?? "";
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
      return [
        ...trimmed,
        {
          seq,
          kind: "tool" as const,
          tool,
          phase: "running" as const,
          title: toolNowLine(tool, toolInput),
          detail: undefined,
          toolInput,
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
            },
          ];
        }
      }
      return base;
    }
    case "tool_result": {
      const tool = event.tool ?? "";
      const ok = event.success ?? false;
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
              }),
              ok,
              progressLines: s.progressLines,
              toolInput: s.toolInput,
              detail: ok
                ? undefined
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
        }),
        ...(!ok && event.message
          ? { detail: String(event.message) }
          : {}),
      });
    }
    case "text_delta":
    case "text_stream": {
      if (streamOpened.current) {
        return prev;
      }
      streamOpened.current = true;
      return push({ kind: "stream", title: "Writing the reply", detail: undefined });
    }
    case "error":
      return push({ kind: "error", title: "Something went wrong", detail: event.error });
    case "done":
      return push({ kind: "done", title: "Done", detail: "Reply ready" });
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
  if (!Number.isFinite(elapsedSec) || elapsedSec < 0) return "Thinking";
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
 * When the first token or tool work arrives, replace the in-flight ``Thinking`` row
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
    if (s?.kind === "thinking" && s.title === "Thinking") {
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
