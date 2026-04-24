import { useMemo, useState } from "react";
import ImageViewer from "./ImageViewer";
import { plotAltFromMeta, plotCaptionFromMeta } from "../plotLabels";
import type { PlotAttachment } from "../types";

interface PlotImageProps {
  src: string;
  alt?: string;
  /** Short label from server ``plot_summaries.title`` — overrides filename-based caption. */
  title?: string;
  /** IANA zone for time-axis charts (``plot_timezone`` / per-plot summary). */
  plotTimezone?: string;
  /** When set, suppresses the time-axis line for charts without a time axis (e.g. FDC). */
  plotType?: string;
  className?: string;
}

export default function PlotImage({
  src,
  alt,
  title,
  plotTimezone,
  plotType,
  className,
}: PlotImageProps) {
  const [open, setOpen] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const resolvedAlt = useMemo(() => plotAltFromMeta(src, alt, title), [src, alt, title]);
  const captionLine = useMemo(() => plotCaptionFromMeta(src, title), [src, title]);

  if (loadError) {
    return (
      <p
        className={`rounded-lg border border-dashed border-brand-border bg-brand-50/80 px-3 py-2 text-xs text-brand-muted dark:border-brand-border dark:bg-brand-100/40 dark:text-brand-muted ${className ?? ""}`}
        role="status"
      >
        This plot is no longer on the server (common after a deploy if plot files are not on
        persistent storage). Run the flow analysis again to regenerate charts, or configure{" "}
        <code className="rounded border border-brand-border/60 bg-white px-1 py-0.5 font-mono text-[0.7rem] text-brand-800 dark:border-brand-border dark:bg-brand-100 dark:text-brand-900">
          PLOTS_DIR
        </code>{" "}
        on a volume.
      </p>
    );
  }

  const showTimeAxisHint = !!plotTimezone && plotType !== "flow_duration_curve";
  const showCaption = captionLine || showTimeAxisHint;

  return (
    <figure className={`my-0 ${className ?? ""}`}>
      <div
        className={`relative w-full overflow-hidden rounded-lg ${!loaded ? "min-h-32" : ""}`}
      >
        {!loaded && (
          <div
            className="pointer-events-none absolute inset-0 z-[1] animate-pulse rounded-lg bg-brand-100/90 dark:bg-brand-100/50"
            aria-hidden
          />
        )}
        <img
          src={src}
          alt={resolvedAlt}
          loading="lazy"
          decoding="async"
          className="relative z-0 w-full cursor-zoom-in opacity-100"
          onClick={() => setOpen(true)}
          onLoad={() => setLoaded(true)}
          onError={() => setLoadError(true)}
        />
      </div>
      {showCaption ? (
        <figcaption className="mt-1.5 text-center text-[0.7rem] leading-snug text-brand-muted">
          {captionLine ? <span className="block">{captionLine}</span> : null}
          {showTimeAxisHint ? (
            <span className="mt-0.5 block text-[0.65rem] text-brand-muted/90">
              Time axes: {plotTimezone}
            </span>
          ) : null}
        </figcaption>
      ) : null}
      {open && !loadError && (
        <ImageViewer src={src} alt={resolvedAlt} onClose={() => setOpen(false)} />
      )}
    </figure>
  );
}

const _PLOT_TYPE_LABELS: Record<string, string> = {
  time_series: "Flow rate",
  flow_duration_curve: "Flow duration curve",
  peaks_annotated: "Demand peaks",
  signal_quality: "Signal quality",
};

function plotTypeLabel(type: string): string {
  return _PLOT_TYPE_LABELS[type] ?? type.replace(/_/g, " ");
}

/**
 * Renders a PlotAttachment list with two layouts:
 *
 * - Single-meter (no groupLabel): vertical stack, same as before.
 * - Batch (groupLabel = serial number): reorganises by plot_type so the same
 *   chart type across all meters appears in a 2-column grid side-by-side.
 *   Each column is labelled with the meter serial number, each section with
 *   the chart type — optimised for visual comparison.
 */
export function PlotGrouped({
  plots,
  className,
}: {
  plots: PlotAttachment[];
  className?: string;
}) {
  const isBatch = plots.some((p) => p.groupLabel);

  // Batch: reorganise by plot_type so same chart type sits side-by-side.
  const typeGroups = useMemo(() => {
    if (!isBatch) return null;
    const order: string[] = [];
    const byType = new Map<string, PlotAttachment[]>();
    for (const p of plots) {
      const key = p.plotType ?? "other";
      if (!byType.has(key)) { byType.set(key, []); order.push(key); }
      byType.get(key)!.push(p);
    }
    return order.map((type) => ({ type, items: byType.get(type)! }));
  }, [plots, isBatch]);

  if (!isBatch || !typeGroups) {
    return (
      <>
        {plots.map((p) => (
          <PlotImage
            key={p.src}
            src={p.src}
            alt="Flow analysis plot"
            title={p.title}
            plotTimezone={p.plotTimezone}
            plotType={p.plotType}
            className={className}
          />
        ))}
      </>
    );
  }

  return (
    <div className="space-y-5">
      {typeGroups.map((g) => (
        <div key={g.type}>
          <p className="mb-2 text-[0.68rem] font-semibold uppercase tracking-wider text-brand-muted">
            {plotTypeLabel(g.type)}
          </p>
          <div className="grid grid-cols-2 gap-2">
            {g.items.map((p) => (
              <div key={p.src}>
                {p.groupLabel && (
                  <p className="mb-1 font-mono text-[0.65rem] font-medium text-brand-muted">
                    {p.groupLabel}
                  </p>
                )}
                <PlotImage
                  src={p.src}
                  alt="Flow analysis plot"
                  title={p.title}
                  plotTimezone={p.plotTimezone}
                  plotType={p.plotType}
                  className={className ?? "w-full rounded-lg shadow-sm"}
                />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
