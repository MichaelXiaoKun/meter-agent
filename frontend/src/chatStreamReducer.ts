import type {
  DownloadArtifact,
  PlotAttachment,
  PlotSummary,
  SSEEvent,
} from "./types";
import { artifactsFromEvent } from "./artifactAttachments";
import {
  applyThinkingElapsed,
  isInflightThinkingStep,
  reduceTurnActivity,
  type TurnActivityStep,
} from "./turnActivity";

export type AgentStatus =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "thinking" }
  | { kind: "queued"; message: string }
  | { kind: "streaming" }
  | { kind: "tool_call"; tool: string }
  | { kind: "tool_progress"; tool: string; message: string }
  | { kind: "tool_result"; tool: string; success: boolean }
  | { kind: "validation"; message: string }
  | { kind: "compressing" }
  | { kind: "rate_limit_wait"; message: string }
  | { kind: "error"; error: string };

export const IDLE: AgentStatus = { kind: "idle" };

interface MutableRefLike<T> {
  current: T;
}

export interface ChatStreamState {
  streamStatus: AgentStatus;
  streamLead: string;
  streamTail: string;
  streamPlots: PlotAttachment[];
  streamArtifacts: DownloadArtifact[];
  turnActivity: TurnActivityStep[];
  workspaceEvents: SSEEvent[];
  assistantError: string | null;
  streamId: string | null;
  turnId: string | null;
  cursor: number;
  streamLeadAccRef: MutableRefLike<string>;
  streamTailAccRef: MutableRefLike<string>;
  sawToolCallRef: MutableRefLike<boolean>;
  plotsRef: MutableRefLike<PlotAttachment[]>;
  artifactsRef: MutableRefLike<DownloadArtifact[]>;
  streamOpenedForTurnRef: MutableRefLike<boolean>;
  thinkingSegmentStartMsRef: MutableRefLike<number | null>;
}

export function createChatStreamState(): ChatStreamState {
  return {
    streamStatus: IDLE,
    streamLead: "",
    streamTail: "",
    streamPlots: [],
    streamArtifacts: [],
    turnActivity: [],
    workspaceEvents: [],
    assistantError: null,
    streamId: null,
    turnId: null,
    cursor: 0,
    streamLeadAccRef: { current: "" },
    streamTailAccRef: { current: "" },
    sawToolCallRef: { current: false },
    plotsRef: { current: [] },
    artifactsRef: { current: [] },
    streamOpenedForTurnRef: { current: false },
    thinkingSegmentStartMsRef: { current: null },
  };
}

export function resetChatStreamStateForTurn(
  state: ChatStreamState,
  opts: {
    status?: AgentStatus;
    streamId?: string | null;
    turnId?: string | null;
    cursor?: number;
    title?: string;
  } = {},
): void {
  state.streamStatus = opts.status ?? { kind: "connecting" };
  state.streamLead = "";
  state.streamTail = "";
  state.streamPlots = [];
  state.streamArtifacts = [];
  state.workspaceEvents = [];
  state.assistantError = null;
  state.streamId = opts.streamId ?? null;
  state.turnId = opts.turnId ?? null;
  state.cursor = Math.max(0, opts.cursor ?? 0);
  state.turnActivity = [
    {
      seq: 0,
      kind: "connecting",
      title: opts.title ?? "Sending your message",
      detail: undefined,
    },
  ];
  state.streamLeadAccRef.current = "";
  state.streamTailAccRef.current = "";
  state.sawToolCallRef.current = false;
  state.streamOpenedForTurnRef.current = false;
  state.thinkingSegmentStartMsRef.current = null;
  state.plotsRef.current = [];
  state.artifactsRef.current = [];
}

export function clearChatStreamStateAfterTurn(
  state: ChatStreamState,
  status: AgentStatus = IDLE,
): void {
  state.streamLead = "";
  state.streamTail = "";
  state.streamLeadAccRef.current = "";
  state.streamTailAccRef.current = "";
  state.sawToolCallRef.current = false;
  state.streamPlots = [];
  state.streamArtifacts = [];
  state.workspaceEvents = [];
  state.plotsRef.current = [];
  state.artifactsRef.current = [];
  state.turnActivity = [];
  state.streamOpenedForTurnRef.current = false;
  state.thinkingSegmentStartMsRef.current = null;
  state.streamId = null;
  state.turnId = null;
  state.cursor = 0;
  state.streamStatus = status;
}

/**
 * Drop SSE events from another turn or duplicate seq values.
 * Events without turn_id still apply for compatibility with older servers.
 */
export function shouldApplyStreamEvent(
  event: SSEEvent,
  expectedTurnId: MutableRefLike<string | null>,
  lastSeq: MutableRefLike<number>,
): boolean {
  const tid = event.turn_id;
  if (
    expectedTurnId.current &&
    tid != null &&
    tid !== "" &&
    tid !== expectedTurnId.current
  ) {
    return false;
  }
  if (typeof event.seq === "number" && !Number.isNaN(event.seq)) {
    if (event.seq <= lastSeq.current) {
      return false;
    }
    lastSeq.current = event.seq;
  }
  return true;
}

export function applyStreamEventToChatState(
  state: ChatStreamState,
  event: SSEEvent,
  refs: {
    expectedTurnId: MutableRefLike<string | null>;
    lastSeq: MutableRefLike<number>;
  },
  options: {
    nowMs?: () => number;
    setTokenUsage?: (usage: { tokens: number; pct: number }) => void;
  } = {},
): { applied: boolean; errorMessage?: string } {
  if (!shouldApplyStreamEvent(event, refs.expectedTurnId, refs.lastSeq)) {
    return { applied: false };
  }
  if (typeof event.seq === "number" && Number.isFinite(event.seq)) {
    state.cursor = Math.max(state.cursor, event.seq);
  }

  const nowMs = options.nowMs ?? (() => Date.now());
  if (event.type === "thinking") {
    state.thinkingSegmentStartMsRef.current = nowMs();
  }
  if (
    event.type === "tool_result" ||
    event.type === "config_confirmation_required" ||
    event.type === "config_confirmation_cancelled" ||
    event.type === "config_confirmation_superseded"
  ) {
    state.workspaceEvents = [...state.workspaceEvents, event];
  }

  let next = reduceTurnActivity(
    state.turnActivity,
    event,
    state.streamOpenedForTurnRef,
  );
  const canCloseThinking =
    event.type === "text_delta" ||
    event.type === "text_stream" ||
    event.type === "tool_call" ||
    event.type === "tool_progress" ||
    event.type === "validation_start" ||
    event.type === "validation_result" ||
    event.type === "rate_limit_wait" ||
    event.type === "tool_result" ||
    event.type === "config_confirmation_required" ||
    event.type === "config_confirmation_cancelled" ||
    event.type === "config_confirmation_superseded" ||
    event.type === "error" ||
    event.type === "tool_round_limit" ||
    event.type === "done";
  if (canCloseThinking) {
    const t0 = state.thinkingSegmentStartMsRef.current;
    const hasOpen = next.some(
      (step) => step != null && isInflightThinkingStep(step),
    );
    if (hasOpen) {
      const elapsedSec = t0 != null ? (nowMs() - t0) / 1000 : 0.1;
      next = applyThinkingElapsed(
        next,
        t0 != null ? Math.max(0.05, elapsedSec) : 0.1,
      );
      if (t0 != null) {
        state.thinkingSegmentStartMsRef.current = null;
      }
    }
  }
  state.turnActivity = next;

  let errorMessage: string | undefined;
  switch (event.type) {
    case "queued":
      state.streamStatus = {
        kind: "queued",
        message: event.message ?? "Waiting for a free slot…",
      };
      break;
    case "intent_route":
      state.streamStatus = { kind: "thinking" };
      break;
    case "thinking":
      if (!state.sawToolCallRef.current) {
        state.streamLeadAccRef.current = "";
        state.streamTailAccRef.current = "";
        state.streamLead = "";
        state.streamTail = "";
      }
      state.streamStatus = { kind: "thinking" };
      break;
    case "text_delta":
    case "text_stream": {
      const chunk = event.text ?? "";
      state.streamStatus = { kind: "streaming" };
      if (!state.sawToolCallRef.current) {
        state.streamLeadAccRef.current += chunk;
        state.streamLead = state.streamLeadAccRef.current;
      } else {
        state.streamTailAccRef.current += chunk;
        state.streamTail = state.streamTailAccRef.current;
      }
      break;
    }
    case "tool_call":
      state.sawToolCallRef.current = true;
      state.streamStatus = { kind: "tool_call", tool: event.tool ?? "" };
      break;
    case "tool_progress":
      state.streamStatus = {
        kind: "tool_progress",
        tool: event.tool ?? "",
        message: event.message ?? "Working…",
      };
      break;
    case "validation_start":
      state.streamStatus = {
        kind: "validation",
        message: event.message ?? "Validating the answer…",
      };
      break;
    case "validation_result":
      state.streamStatus = {
        kind: "validation",
        message:
          event.message ??
          (event.verdict === "needs_experiment"
            ? "Needs more evidence."
            : "Validation complete."),
      };
      break;
    case "tool_result": {
      state.streamStatus = {
        kind: "tool_result",
        tool: event.tool ?? "",
        success: event.success ?? false,
      };
      const merged: PlotAttachment[] = [];
      if (event.meters?.length) {
        for (const meter of event.meters) {
          const mPaths = meter.plot_paths ?? [];
          if (!mPaths.length) continue;
          const mSums = meter.plot_summaries;
          const mTz = meter.plot_timezone;
          for (let i = 0; i < mPaths.length; i++) {
            const raw = mPaths[i];
            const filename = raw.split("/").pop() ?? raw;
            const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
            const s = mSums?.find((x) => x.filename === filename) ?? mSums?.[i];
            merged.push({
              src,
              title: s?.title,
              plotTimezone: s?.plot_timezone ?? mTz,
              plotType: s?.plot_type,
              caption: s?.caption,
              groupLabel: meter.serial_number,
            });
          }
        }
      } else {
        const paths = event.plot_paths;
        if (paths?.length) {
          const summaries = event.plot_summaries as PlotSummary[] | undefined;
          const fallbackTz = event.plot_timezone;
          for (let i = 0; i < paths.length; i++) {
            const raw = paths[i];
            const filename = raw.split("/").pop() ?? raw;
            const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
            const s = summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
            merged.push({
              src,
              title: s?.title,
              plotTimezone: s?.plot_timezone ?? fallbackTz,
              plotType: s?.plot_type,
              caption: s?.caption,
            });
          }
        }
      }
      if (merged.length) {
        state.plotsRef.current = [...state.plotsRef.current, ...merged];
        state.streamPlots = [...state.plotsRef.current];
      }
      const artifacts = artifactsFromEvent(event);
      if (artifacts.length) {
        state.artifactsRef.current = [
          ...state.artifactsRef.current,
          ...artifacts,
        ];
        state.streamArtifacts = [...state.artifactsRef.current];
      }
      break;
    }
    case "config_confirmation_required":
    case "config_confirmation_cancelled":
    case "config_confirmation_superseded":
      state.streamStatus = {
        kind: "tool_result",
        tool: event.tool ?? "configuration",
        success: true,
      };
      break;
    case "token_usage": {
      options.setTokenUsage?.({
        tokens: event.tokens ?? 0,
        pct: event.pct ?? 0,
      });
      if (state.streamStatus.kind === "connecting") {
        state.streamStatus = { kind: "thinking" };
      }
      break;
    }
    case "compressing":
      state.streamStatus = { kind: "compressing" };
      break;
    case "rate_limit_wait":
      state.streamStatus = {
        kind: "rate_limit_wait",
        message: event.message ?? "Waiting for input-token headroom…",
      };
      break;
    case "tool_round_limit": {
      const lim = event.limit ?? 0;
      const msg =
        lim > 0
          ? `Stopped after ${lim} assistant steps (safety limit).`
          : "Stopped: assistant step limit reached.";
      state.streamStatus = { kind: "error", error: msg };
      errorMessage = msg;
      break;
    }
    case "error": {
      const msg = event.error ?? "Unknown error";
      state.assistantError = msg;
      state.streamStatus = { kind: "error", error: msg };
      errorMessage = msg;
      break;
    }
    case "done":
      break;
  }

  return { applied: true, errorMessage };
}
