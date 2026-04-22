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
}

export interface SSEEvent {
  type:
    | "text_delta"
    | "tool_call"
    | "tool_result"
    | "tool_progress"
    | "thinking"
    | "token_usage"
    | "compressing"
    | "queued"
    | "done"
    | "error";
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  success?: boolean;
  plot_paths?: string[];
  plot_summaries?: PlotSummary[];
  plot_timezone?: string;
  message?: string;
  tokens?: number;
  pct?: number;
  error?: string;
  /** Present on events from orchestrator — same id for one user message / chat POST. */
  turn_id?: string;
  /** Monotonic per turn — drop duplicates or stale ordering bugs. */
  seq?: number;
}
