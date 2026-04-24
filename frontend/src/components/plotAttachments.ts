import type { ContentBlock, PlotAttachment } from "../types";

type SummaryEntry = {
  filename?: string;
  title?: string;
  plot_timezone?: string;
  plot_type?: string;
};

function resolveAttachment(
  raw: unknown,
  i: number,
  summaries: SummaryEntry[] | undefined,
  fallbackTz: string | undefined,
  groupLabel?: string,
): PlotAttachment | null {
  if (typeof raw !== "string") return null;
  const filename = raw.split("/").pop() ?? raw;
  const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
  const s = summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
  return {
    src,
    title: typeof s?.title === "string" ? s.title : undefined,
    plotTimezone: (typeof s?.plot_timezone === "string" ? s.plot_timezone : undefined) ?? fallbackTz,
    plotType: typeof s?.plot_type === "string" ? s.plot_type : undefined,
    groupLabel,
  };
}

/**
 * Build ``/api/plots/…`` URLs plus optional labels from persisted ``tool_result``
 * JSON. Handles both ``analyze_flow_data`` (flat) and ``batch_analyze_flow``
 * (per-meter ``meters`` array, sets ``groupLabel`` for grouped rendering).
 */
export function extractPlotAttachments(content: string | ContentBlock[]): PlotAttachment[] {
  if (typeof content === "string") return [];
  const out: PlotAttachment[] = [];
  for (const block of content) {
    if (block.type !== "tool_result" || !block.content) continue;
    try {
      const result = JSON.parse(block.content) as {
        plot_paths?: unknown;
        plot_summaries?: SummaryEntry[];
        plot_timezone?: string;
        meters?: Array<{
          serial_number?: string;
          plot_paths?: unknown;
          plot_summaries?: SummaryEntry[];
          plot_timezone?: string;
        }>;
      };

      // batch_analyze_flow: per-meter structure → set groupLabel for each plot.
      if (Array.isArray(result.meters) && result.meters.length > 0) {
        for (const meter of result.meters) {
          const mPaths = Array.isArray(meter.plot_paths) ? meter.plot_paths : [];
          if (!mPaths.length) continue;
          const mSums = Array.isArray(meter.plot_summaries) ? meter.plot_summaries : undefined;
          const mTz = typeof meter.plot_timezone === "string" ? meter.plot_timezone : undefined;
          const serial = typeof meter.serial_number === "string" ? meter.serial_number : undefined;
          for (let i = 0; i < mPaths.length; i++) {
            const a = resolveAttachment(mPaths[i], i, mSums, mTz, serial);
            if (a) out.push(a);
          }
        }
        continue;
      }

      // analyze_flow_data: flat plot_paths.
      const rawPaths = result.plot_paths;
      if (!Array.isArray(rawPaths) || rawPaths.length === 0) continue;
      const summaries = Array.isArray(result.plot_summaries) ? result.plot_summaries : undefined;
      const fallbackTz = typeof result.plot_timezone === "string" ? result.plot_timezone : undefined;
      for (let i = 0; i < rawPaths.length; i++) {
        const a = resolveAttachment(rawPaths[i], i, summaries, fallbackTz);
        if (a) out.push(a);
      }
    } catch {
      /* skip malformed tool JSON */
    }
  }
  return out;
}

/** @deprecated Prefer ``extractPlotAttachments`` — kept for call sites that only need URLs. */
export function extractPlotPaths(content: string | ContentBlock[]): string[] {
  return extractPlotAttachments(content).map((p) => p.src);
}
