import { useMemo, useState } from "react";
import ImageViewer from "./ImageViewer";
import { plotAltFromMeta, plotCaptionFromMeta } from "../plotLabels";
import type { DiagnosticMarker, PlotAttachment, PlotCaption } from "../types";

interface PlotImageProps {
  src: string;
  alt?: string;
  /** Short label from server ``plot_summaries.title`` — overrides filename-based caption. */
  title?: string;
  /** IANA zone for time-axis charts (``plot_timezone`` / per-plot summary). */
  plotTimezone?: string;
  /** When set, suppresses the time-axis line for charts without a time axis (e.g. FDC). */
  plotType?: string;
  caption?: PlotCaption;
  className?: string;
}

export default function PlotImage({
  src,
  alt,
  title,
  plotTimezone,
  plotType,
  caption,
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
      <DiagnosticMarkerPanel caption={caption} />
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
  diagnostic_timeline: "Diagnostic timeline",
};

function severityClass(severity: string | undefined): string {
  if (severity === "high") {
    return "border-red-300 bg-red-50 text-red-800 dark:border-red-400/40 dark:bg-red-950/30 dark:text-red-200";
  }
  if (severity === "medium") {
    return "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-400/40 dark:bg-amber-950/30 dark:text-amber-200";
  }
  return "border-brand-border bg-brand-50 text-brand-700 dark:border-brand-border dark:bg-white/[0.05] dark:text-brand-300";
}

function markerTime(marker: DiagnosticMarker): string {
  const fmt = (v: number | undefined) =>
    typeof v === "number" && Number.isFinite(v)
      ? new Date(v * 1000).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        })
      : "";
  if (marker.timestamp != null) return fmt(marker.timestamp);
  const start = fmt(marker.start);
  const end = fmt(marker.end);
  if (start && end) return `${start} to ${end}`;
  return start || end;
}

function DiagnosticMarkerPanel({ caption }: { caption?: PlotCaption }) {
  const markers = Array.isArray(caption?.diagnostic_markers)
    ? caption.diagnostic_markers
    : [];
  if (!markers.length) return null;

  const shown = markers.slice(0, 4);
  const hidden = markers.length - shown.length;
  const actions = Array.isArray(caption?.next_actions)
    ? caption.next_actions.filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    : [];

  return (
    <div className="mt-3 rounded-lg border border-brand-border bg-brand-50/80 p-3 text-left dark:bg-white/[0.04]">
      <p className="text-[0.68rem] font-semibold uppercase tracking-wider text-brand-muted">
        What this chart is showing
      </p>
      {typeof caption?.summary === "string" && caption.summary.trim() ? (
        <p className="mt-1 text-xs leading-relaxed text-brand-800 dark:text-brand-900">
          {caption.summary}
        </p>
      ) : null}
      <div className="mt-2 space-y-2">
        {shown.map((marker, idx) => (
          <div
            key={`${marker.type}-${marker.timestamp ?? marker.start ?? idx}-${marker.source}`}
            className="rounded-md border border-brand-border/70 bg-white/70 p-2 dark:bg-brand-100/40"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-semibold text-brand-900">{marker.label}</span>
              {marker.severity ? (
                <span
                  className={`rounded border px-1.5 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide ${severityClass(marker.severity)}`}
                >
                  {marker.severity}
                </span>
              ) : null}
              <span className="font-mono text-[0.62rem] uppercase tracking-wide text-brand-muted">
                {marker.source}
              </span>
            </div>
            <p className="mt-1 text-xs leading-snug text-brand-muted">
              {marker.explanation}
            </p>
            {markerTime(marker) ? (
              <p className="mt-1 font-mono text-[0.65rem] text-brand-muted/90">
                {markerTime(marker)}
              </p>
            ) : null}
          </div>
        ))}
      </div>
      {hidden > 0 ? (
        <p className="mt-2 text-[0.7rem] text-brand-muted">+{hidden} more marker(s)</p>
      ) : null}
      {actions.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {actions.slice(0, 3).map((action) => (
            <span
              key={action}
              className="rounded-full border border-brand-border px-2 py-1 text-[0.68rem] text-brand-muted"
            >
              {action}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

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
            caption={p.caption}
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
                  caption={p.caption}
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
