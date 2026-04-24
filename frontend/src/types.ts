export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: string;
  /** Persisted turn timeline for replay in history. */
  v?: number;
  events?: Array<Record<string, unknown>>;
}

export interface Message {
  role: "user" | "assistant";
  content: string | ContentBlock[];
}

/** One plot file from ``analyze_flow_data`` — matches ``plot_paths`` order. */
export interface PlotSummary {
  filename: string;
  plot_type: string;
  title: string;
  plot_timezone: string;
}

/** Resolved URL + optional labels for :component:`PlotImage`. */
export interface PlotAttachment {
  src: string;
  title?: string;
  plotTimezone?: string;
  /** From ``plot_summaries.plot_type`` — used to hide time-axis hint for non-temporal charts. */
  plotType?: string;
  /** Serial number — set for ``batch_analyze_flow`` results to enable per-meter grouping. */
  groupLabel?: string;
}

export interface SSEEvent {
  type:
  | "text_delta"
  | "text_stream"
  | "tool_call"
  | "tool_result"
  | "tool_progress"
  | "thinking"
  | "token_usage"
  | "compressing"
  | "queued"
  | "intent_route"
  | "tool_round_limit"
  | "done"
  | "error";
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  success?: boolean;
  /** Success-only: full activity timeline title from the server. */
  tool_activity?: string;
  plot_paths?: string[];
  plot_summaries?: PlotSummary[];
  plot_timezone?: string;
  /** Present on ``batch_analyze_flow`` tool_result events — used for per-meter plot grouping. */
  meters?: Array<{
    serial_number: string;
    plot_paths?: string[];
    plot_summaries?: PlotSummary[];
    plot_timezone?: string;
  }>;
  message?: string;
  tokens?: number;
  pct?: number;
  error?: string;
  /** When ``type`` is ``intent_route`` — cheap routing pass before the main model call. */
  intent?: string;
  source?: string;
  tools?: string[];
  rate_limit_wait_seconds?: number;
  attempt?: number;
  model?: string;
  /** When ``type`` is ``tool_round_limit`` — ORCHESTRATOR_MAX_TOOL_ROUNDS cap. */
  limit?: number;
  /** Same-turn dedupe: identical analyze_flow_data reused without re-running subprocess. */
  deduped?: boolean;
  /** Present on events from orchestrator — same id for one user message / chat POST. */
  turn_id?: string;
  /** Monotonic per turn — drop duplicates or stale ordering bugs. */
  seq?: number;
}
