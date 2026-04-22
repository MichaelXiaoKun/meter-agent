import type { ContentBlock, PlotAttachment } from "../types";

/**
 * Build ``/api/plots/…`` URLs plus optional labels from persisted ``tool_result``
 * JSON (``plot_summaries`` / ``plot_timezone`` from ``analyze_flow_data``).
 */
export function extractPlotAttachments(content: string | ContentBlock[]): PlotAttachment[] {
  if (typeof content === "string") return [];
  const out: PlotAttachment[] = [];
  for (const block of content) {
    if (block.type !== "tool_result" || !block.content) continue;
    try {
      const result = JSON.parse(block.content) as {
        plot_paths?: unknown;
        plot_summaries?: Array<{
          filename?: string;
          title?: string;
          plot_timezone?: string;
          plot_type?: string;
        }>;
        plot_timezone?: string;
      };
      const rawPaths = result.plot_paths;
      if (!Array.isArray(rawPaths) || rawPaths.length === 0) continue;
      const summaries = Array.isArray(result.plot_summaries) ? result.plot_summaries : undefined;
      const fallbackTz =
        typeof result.plot_timezone === "string" ? result.plot_timezone : undefined;

      for (let i = 0; i < rawPaths.length; i++) {
        const raw = rawPaths[i];
        if (typeof raw !== "string") continue;
        const filename = raw.split("/").pop() ?? raw;
        const src = raw.startsWith("/api/") ? raw : `/api/plots/${filename}`;
        const s =
          summaries?.find((x) => x.filename === filename) ?? summaries?.[i];
        out.push({
          src,
          title: typeof s?.title === "string" ? s.title : undefined,
          plotTimezone:
            (typeof s?.plot_timezone === "string" ? s.plot_timezone : undefined) ?? fallbackTz,
          plotType: typeof s?.plot_type === "string" ? s.plot_type : undefined,
        });
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
