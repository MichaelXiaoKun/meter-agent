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
export interface DiagnosticMarker {
  type: string;
  label: string;
  severity?: "low" | "medium" | "high" | string;
  timestamp?: number;
  start?: number;
  end?: number;
  explanation: string;
  source: string;
}

export interface PlotCaption {
  plot_type?: string;
  summary?: string;
  diagnostic_markers?: DiagnosticMarker[];
  marker_count?: number;
  next_actions?: string[];
  [key: string]: unknown;
}

export interface PlotSummary {
  filename: string;
  plot_type: string;
  title: string;
  plot_timezone: string;
  caption?: PlotCaption;
}

/** Resolved URL + optional labels for :component:`PlotImage`. */
export interface PlotAttachment {
  src: string;
  title?: string;
  plotTimezone?: string;
  /** From ``plot_summaries.plot_type`` — used to hide time-axis hint for non-temporal charts. */
  plotType?: string;
  caption?: PlotCaption;
  /** Serial number — set for ``batch_analyze_flow`` results to enable per-meter grouping. */
  groupLabel?: string;
}

export interface DownloadArtifact {
  kind: "csv";
  title: string;
  filename: string;
  url: string;
  rowCount?: number;
  /** Serial number — set for ``batch_analyze_flow`` results. */
  groupLabel?: string;
}

export interface SSEEvent {
  type:
  | "text_delta"
  | "text_stream"
  | "tool_call"
  | "tool_result"
  | "tool_progress"
  | "config_confirmation_required"
  | "config_confirmation_cancelled"
  | "config_confirmation_superseded"
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
  /** Human-readable wall-clock range from tool output. */
  display_range?: string;
  /** True when a long report was shortened before being sent to the outer model/UI. */
  report_truncated?: boolean;
  /** Present for flow-analysis tool results when the subprocess wrote an audit bundle. */
  analysis_json_path?: string;
  /** Small processor summaries for the activity timeline, e.g. CUSUM drift. */
  analysis_details?: {
    cusum_drift?: {
      skipped?: boolean;
      drift_detected?: string | null;
      positive_alarm_count?: number | null;
      negative_alarm_count?: number | null;
      first_alarm_timestamp?: number | null;
      adequacy_ok?: boolean | null;
      adequacy_reason?: string | null;
      actual_points?: number | null;
      target_min?: number | null;
      gap_pct?: number | null;
    };
    attribution?: Record<string, unknown> | null;
  };
  /** Structured meter facts for the workspace panel. */
  meter_context?: {
    serial_number?: string;
    label?: string | null;
    network_type?: string | null;
    timezone?: string | null;
    online?: boolean | null;
    last_message_at?: string | null;
    signal?: Record<string, unknown> | null;
    pipe_config?: Record<string, unknown> | null;
    installed?: boolean | null;
    commissioned?: boolean | null;
    active?: boolean | null;
  };
  /** User-facing diagnostic facts for the workspace panel. */
  diagnostic_summary?: {
    kind?: string;
    range?: string | null;
    online?: boolean | null;
    last_message_at?: string | null;
    communication_status?: string | null;
    signal?: Record<string, unknown> | null;
    pipe_config?: Record<string, unknown> | null;
    adequacy?: Record<string, unknown> | null;
    attribution?: Record<string, unknown> | null;
    drift?: Record<string, unknown> | null;
    alarms?: Record<string, unknown> | null;
    plot_count?: number | null;
    plot_explanation?: {
      summary?: string | null;
      markers?: DiagnosticMarker[];
      next_actions?: string[];
    } | null;
    next_actions?: string[];
  };
  /** Confirmation/execution state for pipe/angle writes. */
  config_workflow?: {
    action_id?: string;
    status?: string;
    tool?: string;
    serial_number?: string;
    proposed_values?: Record<string, unknown>;
    current_values?: Record<string, unknown> | null;
    verification?: Record<string, unknown> | null;
    created_at?: number;
    expires_at?: number;
    expires_in_seconds?: number;
    message?: string;
    risk?: string;
  };
  plot_paths?: string[];
  plot_summaries?: PlotSummary[];
  plot_timezone?: string;
  download_artifacts?: DownloadArtifact[];
  /** Present on ``batch_analyze_flow`` tool_result events — used for per-meter plot grouping. */
  meters?: Array<{
    serial_number: string;
    plot_paths?: string[];
    plot_summaries?: PlotSummary[];
    plot_timezone?: string;
    download_artifacts?: DownloadArtifact[];
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
