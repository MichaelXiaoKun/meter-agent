/**
 * Plot PNGs are saved as ``{serial}_{epochStart}_{plot_type}.png`` (see
 * ``data-processing-agent/processors/plots._save``). Derive readable labels
 * for accessibility and captions when callers only pass a generic alt.
 */
const PLOT_TYPE_LABELS: Record<string, string> = {
  time_series: "Flow rate (time series)",
  flow_duration_curve: "Flow duration curve",
  peaks_annotated: "Demand peaks",
  signal_quality: "Signal quality",
};

/** ``basename`` without directory or ``.png`` suffix. */
function parsePlotFilename(basename: string): { serial: string; plotType: string } | null {
  const base = basename.replace(/\.png$/i, "");
  const parts = base.split("_");
  if (parts.length < 3) return null;
  const plotType = parts[parts.length - 1] ?? "";
  const serial = parts.slice(0, -2).join("_");
  if (!plotType || !serial) return null;
  return { serial, plotType };
}

export function plotCaptionFromSrc(src: string): string | null {
  const name = src.split("/").pop();
  if (!name) return null;
  const parsed = parsePlotFilename(name);
  if (!parsed) return null;
  const known = PLOT_TYPE_LABELS[parsed.plotType];
  if (known) return known;
  return parsed.plotType
    .split("_")
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ");
}

export function plotAltFromSrc(src: string, explicitAlt?: string): string {
  if (explicitAlt && explicitAlt.trim() && explicitAlt !== "Flow analysis plot") {
    return explicitAlt.trim();
  }
  const name = src.split("/").pop();
  if (!name) return explicitAlt?.trim() || "Analysis plot";
  const parsed = parsePlotFilename(name);
  if (!parsed) return explicitAlt?.trim() || "Analysis plot";
  const kind =
    PLOT_TYPE_LABELS[parsed.plotType] ??
    parsed.plotType.replace(/_/g, " ");
  return `${kind} — meter ${parsed.serial}`;
}

/** Prefer server ``title`` (from ``plot_summaries``) over filename heuristics. */
export function plotCaptionFromMeta(src: string, serverTitle?: string | null): string | null {
  if (serverTitle?.trim()) return serverTitle.trim();
  return plotCaptionFromSrc(src);
}

export function plotAltFromMeta(
  src: string,
  explicitAlt: string | undefined,
  serverTitle: string | null | undefined,
): string {
  if (explicitAlt && explicitAlt.trim() && explicitAlt !== "Flow analysis plot") {
    return explicitAlt.trim();
  }
  if (serverTitle?.trim()) {
    const name = src.split("/").pop();
    const parsed = name ? parsePlotFilename(name) : null;
    return parsed ? `${serverTitle.trim()} — meter ${parsed.serial}` : serverTitle.trim();
  }
  return plotAltFromSrc(src, explicitAlt);
}
