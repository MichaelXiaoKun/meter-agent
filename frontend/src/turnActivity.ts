import type { MutableRefObject } from "react";
import type { SSEEvent } from "./types";

/** User-visible labels — keep in sync with StatusIndicator TOOL_LABELS conceptually */
export const TOOL_PHASE_TITLE: Record<string, string> = {
  resolve_time_range: "Resolving time range",
  check_meter_status: "Checking meter status",
  get_meter_profile: "Fetching meter profile",
  analyze_flow_data: "Analyzing flow data",
  configure_meter_pipe: "Configuring meter pipe",
  set_transducer_angle_only: "Setting transducer angle (SSA only)",
};

export interface TurnActivityStep {
  seq: number;
  kind:
    | "queued"
    | "thinking"
    | "context"
    | "compressing"
    | "tool_call"
    | "tool_progress"
    | "tool_result"
    | "stream"
    | "done"
    | "error";
  title: string;
  detail?: string;
  tool?: string;
  ok?: boolean;
}

function toolTitle(tool: string): string {
  return TOOL_PHASE_TITLE[tool] ?? tool.replace(/_/g, " ");
}

/**
 * Append or update steps from one SSE event. Keeps order aligned with seq.
 */
export function reduceTurnActivity(
  prev: TurnActivityStep[],
  event: SSEEvent,
  streamOpened: MutableRefObject<boolean>
): TurnActivityStep[] {
  const seq = typeof event.seq === "number" ? event.seq : prev.length;

  const push = (step: Omit<TurnActivityStep, "seq"> & { seq?: number }) =>
    [...prev, { ...step, seq: step.seq ?? seq } as TurnActivityStep];

  switch (event.type) {
    case "queued":
      return push({
        kind: "queued",
        title: "In queue",
        detail: event.message ?? "Waiting for another turn to finish",
      });
    case "thinking": {
      const last = prev[prev.length - 1];
      if (last?.kind === "thinking") {
        return prev;
      }
      return push({
        kind: "thinking",
        title: prev.length === 0 ? "Preparing request" : "Preparing next step",
        detail: "Waiting on Claude…",
      });
    }
    case "token_usage": {
      const pct = Math.round((event.pct ?? 0) * 100);
      const tokens = (event.tokens ?? 0).toLocaleString();
      const row: TurnActivityStep = {
        seq,
        kind: "context",
        title: "Conversation context",
        detail: `~${pct}% of model window · ${tokens} input tokens (estimate)`,
      };
      return [...prev.filter((p) => p.kind !== "context"), row];
    }
    case "compressing":
      return push({
        kind: "compressing",
        title: "Compressing history",
        detail: "Summarizing older messages to stay within limits",
      });
    case "tool_call": {
      const tool = event.tool ?? "";
      return push({
        kind: "tool_call",
        title: toolTitle(tool),
        detail: "Calling tool…",
        tool,
      });
    }
    case "tool_progress": {
      const tool = event.tool ?? "";
      const msg = event.message ?? "";
      const last = prev[prev.length - 1];
      if (last?.kind === "tool_progress" && last.tool === tool) {
        return [
          ...prev.slice(0, -1),
          {
            ...last,
            seq,
            title: toolTitle(tool),
            detail: msg,
            tool,
          },
        ];
      }
      return push({
        kind: "tool_progress",
        title: toolTitle(tool),
        detail: msg,
        tool,
      });
    }
    case "tool_result": {
      const tool = event.tool ?? "";
      const ok = event.success ?? false;
      return push({
        kind: "tool_result",
        title: `${toolTitle(tool)} — ${ok ? "finished" : "failed"}`,
        ok,
        tool,
      });
    }
    case "text_delta": {
      if (streamOpened.current) {
        return prev;
      }
      streamOpened.current = true;
      return push({
        kind: "stream",
        title: "Generating reply",
        detail: "Streaming assistant response",
      });
    }
    case "error":
      return push({
        kind: "error",
        title: "Request failed",
        detail: event.error,
      });
    case "done":
      return push({
        kind: "done",
        title: "Turn complete",
        detail: "Response saved to this conversation",
      });
    default:
      return prev;
  }
}
