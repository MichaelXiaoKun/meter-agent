import type { ContentBlock, Message, PlotAttachment, PlotSummary, SSEEvent } from "./types";

export interface MeterWorkspaceState {
  serialNumber?: string;
  label?: string;
  networkType?: string;
  timezone?: string;
  online?: boolean | null;
  lastMessageAt?: string | null;
  signal?: Record<string, unknown> | null;
  pipeConfig?: Record<string, unknown> | null;
  flow?: {
    range?: string | null;
    adequacyExplanation?: string;
    adequacy?: Record<string, unknown> | null;
    attribution?: Record<string, unknown> | null;
    drift?: Record<string, unknown> | null;
    alarms?: Record<string, unknown> | null;
    plotCount?: number | null;
    plotExplanation?: NonNullable<NonNullable<SSEEvent["diagnostic_summary"]>["plot_explanation"]> | null;
    plots?: PlotAttachment[];
  };
  pendingConfig?: NonNullable<SSEEvent["config_workflow"]> | null;
  lastConfig?: NonNullable<SSEEvent["config_workflow"]> | null;
  nextActions: string[];
  updatedAt?: number;
}

const EMPTY: MeterWorkspaceState = { nextActions: [] };

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function fmtCount(v: unknown): string | undefined {
  const n = num(v);
  return n == null ? undefined : Math.round(n).toLocaleString();
}

function fmtPct(v: unknown): string | undefined {
  const n = num(v);
  return n == null ? undefined : `${Math.round(n * 10) / 10}%`;
}

export function adequacyExplanation(adequacy: Record<string, unknown> | null | undefined): string {
  if (!adequacy) return "";
  const actual = fmtCount(adequacy.actual_points);
  const target = fmtCount(adequacy.target_min);
  const gaps = fmtPct(adequacy.gap_pct);
  const bits = [
    actual && target ? `${actual} samples available, ${target} required` : "",
    gaps ? `${gaps} gaps` : "",
  ].filter(Boolean);
  const suffix = bits.length ? `: ${bits.join(", ")}` : ".";
  if (adequacy.ok === false) {
    const reason = str(adequacy.reason) ?? "not enough usable data";
    return `CUSUM was skipped because ${reason}${bits.length ? `: ${bits.join(", ")}` : "."}`;
  }
  return str(adequacy.explanation) ?? `Data is sufficient for drift detection${suffix}`;
}

function cleanActions(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return values
    .filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    .map((v) => v.trim())
    .slice(0, 4);
}

function resolvePlotAttachment(
  raw: unknown,
  i: number,
  summaries: PlotSummary[] | undefined,
  fallbackTz: string | undefined,
): PlotAttachment | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  const filename = raw.split("/").pop() ?? raw;
  const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
  const s = summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
  return {
    src,
    title: s?.title,
    plotTimezone: s?.plot_timezone ?? fallbackTz,
    plotType: s?.plot_type,
    caption: s?.caption,
  };
}

function plotsFromEvent(event: SSEEvent): PlotAttachment[] {
  const paths = event.plot_paths;
  if (!Array.isArray(paths) || paths.length === 0) return [];
  const summaries = Array.isArray(event.plot_summaries) ? event.plot_summaries : undefined;
  return paths
    .map((p, i) => resolvePlotAttachment(p, i, summaries, event.plot_timezone))
    .filter((p): p is PlotAttachment => p != null);
}

function eventsFromContent(content: string | ContentBlock[]): SSEEvent[] {
  if (!Array.isArray(content)) return [];
  const out: SSEEvent[] = [];
  for (const block of content) {
    if (block.type === "turn_activity" && Array.isArray(block.events)) {
      for (const raw of block.events) {
        if (raw && typeof raw === "object") out.push(raw as unknown as SSEEvent);
      }
    }
  }
  return out;
}

export function workspaceEventsFromMessages(messages: Message[]): SSEEvent[] {
  const out: SSEEvent[] = [];
  for (const msg of messages) {
    if (msg.role !== "assistant") continue;
    out.push(...eventsFromContent(msg.content));
  }
  return out;
}

export function reduceWorkspaceEvent(
  state: MeterWorkspaceState,
  event: SSEEvent,
): MeterWorkspaceState {
  let next: MeterWorkspaceState = { ...state, nextActions: [...state.nextActions] };
  const now = typeof event.seq === "number" ? event.seq : Date.now();

  const ctx = event.meter_context;
  if (ctx) {
    next = {
      ...next,
      serialNumber: str(ctx.serial_number) ?? next.serialNumber,
      label: str(ctx.label) ?? next.label,
      networkType: str(ctx.network_type) ?? next.networkType,
      timezone: str(ctx.timezone) ?? next.timezone,
      online: ctx.online ?? next.online,
      lastMessageAt: str(ctx.last_message_at) ?? next.lastMessageAt,
      signal: ctx.signal ?? next.signal,
      pipeConfig: ctx.pipe_config ?? next.pipeConfig,
      updatedAt: now,
    };
  }

  const summary = event.diagnostic_summary;
  if (summary?.kind === "status") {
    next = {
      ...next,
      online: summary.online ?? next.online,
      lastMessageAt: str(summary.last_message_at) ?? next.lastMessageAt,
      signal: summary.signal ?? next.signal,
      pipeConfig: summary.pipe_config ?? next.pipeConfig,
      nextActions: cleanActions(summary.next_actions),
      updatedAt: now,
    };
  } else if (summary?.kind === "flow") {
    const adequacy = summary.adequacy ?? null;
    const plots = plotsFromEvent(event);
    next = {
      ...next,
      flow: {
        range: summary.range,
        adequacy,
        attribution: summary.attribution ?? null,
        adequacyExplanation: adequacyExplanation(adequacy),
        drift: summary.drift ?? null,
        alarms: summary.alarms ?? null,
        plotCount: summary.plot_count,
        plotExplanation: summary.plot_explanation ?? state.flow?.plotExplanation ?? null,
        plots: plots.length ? plots : state.flow?.plots ?? [],
      },
      nextActions: cleanActions(summary.next_actions),
      updatedAt: now,
    };
  }

  const workflow = event.config_workflow;
  if (workflow?.action_id) {
    const status = str(workflow.status);
    if (status === "pending_confirmation") {
      next = { ...next, pendingConfig: workflow, updatedAt: now };
    } else if (
      status === "executed" ||
      status === "verified" ||
      status === "failed" ||
      status === "verification_failed" ||
      status === "superseded"
    ) {
      next = {
        ...next,
        pendingConfig:
          state.pendingConfig?.action_id === workflow.action_id ? null : state.pendingConfig,
        lastConfig: workflow,
        updatedAt: now,
      };
    }
  }

  return next;
}

export function buildMeterWorkspace(
  messages: Message[],
  liveEvents: SSEEvent[] = [],
): MeterWorkspaceState {
  const events = [...workspaceEventsFromMessages(messages), ...liveEvents];
  return events.reduce(reduceWorkspaceEvent, EMPTY);
}

export function driftLabel(flow: MeterWorkspaceState["flow"]): string {
  const drift = flow?.drift;
  if (!drift) return "Not checked";
  if (drift.skipped === true) return "Skipped";
  const direction = str(drift.direction) ?? "none";
  return direction === "none" ? "No sustained drift" : `${direction} drift`;
}

const ATTRIBUTION_LABELS: Record<string, string> = {
  real_flow_change: "Real flow change",
  possible_leak_or_baseline_rise: "Possible leak or baseline rise",
  sensor_or_install_issue: "Sensor or install issue",
  communications_or_sampling_issue: "Communications or sampling issue",
  insufficient_data: "Insufficient data",
  normal: "Normal",
};

export function attributionLabel(attribution: Record<string, unknown> | null | undefined): string {
  if (!attribution) return "No interpretation yet";
  const raw = str(attribution.primary_type);
  return raw ? (ATTRIBUTION_LABELS[raw] ?? raw.replaceAll("_", " ")) : "No interpretation yet";
}

export function severityLabel(attribution: Record<string, unknown> | null | undefined): string {
  const raw = str(attribution?.severity);
  return raw ? raw[0].toUpperCase() + raw.slice(1) : "Unknown";
}

export function confidenceLabel(attribution: Record<string, unknown> | null | undefined): string {
  const raw = str(attribution?.confidence);
  return raw ? raw[0].toUpperCase() + raw.slice(1) : "Unknown";
}

export function signalLabel(signal: Record<string, unknown> | null | undefined): string {
  if (!signal) return "Unknown";
  const level = str(signal.level);
  const score = num(signal.score);
  if (level && score != null) return `${level} (${Math.round(score)})`;
  return level ?? (score != null ? String(Math.round(score)) : "Unknown");
}
