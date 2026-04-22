import type { MutableRefObject } from "react";
import type { SSEEvent } from "./types";

/**
 * One line per tool: present-continuous (running) and past (done), kept parallel for alignment.
 * Public for StatusIndicator to stay in sync with the timeline.
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

/** User-visible line while a tool is running (fallback for unknown tool names from the server). */
export function toolNowLine(name: string): string {
  const t = name.trim() || "tool";
  if (TOOL_LIFECYCLE[t]) return TOOL_LIFECYCLE[t].now;
  const n = t.replace(/_/g, " ");
  return n ? `Running ${n}…` : "Running a tool…";
}

export function toolDoneLine(name: string, ok: boolean): string {
  if (!ok) return "Tool run failed";
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
}

/**
 * After the first SSE event, drop the client-only “connecting” step so the
 * timeline shows server-driven stages.
 */
function withoutConnecting(prev: TurnActivityStep[]): TurnActivityStep[] {
  if (prev.length === 1 && prev[0].kind === "connecting") {
    return [];
  }
  return prev;
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
      return push({
        kind: "tool",
        tool,
        phase: "running",
        title: toolNowLine(tool),
        detail: undefined,
      });
    }
    case "tool_progress": {
      const tool = event.tool ?? "";
      const msg = (event.message ?? "").trim();
      if (!msg) return base;
      const i = base.length;
      for (let k = i - 1; k >= 0; k -= 1) {
        const s = base[k];
        if (s?.kind === "tool" && s.tool === tool && s.phase === "running") {
          return [
            ...base.slice(0, k),
            {
              ...s,
              seq,
              title: s.title,
              detail: msg.length > 72 ? `${msg.slice(0, 69).trimEnd()}…` : msg,
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
              title: toolDoneLine(tool, ok),
              ok,
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
        title: toolDoneLine(tool, ok),
        ...(!ok && event.message
          ? { detail: String(event.message) }
          : {}),
      });
    }
    case "text_delta": {
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
