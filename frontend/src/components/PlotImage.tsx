import { useMemo, useState } from "react";
import ImageViewer from "./ImageViewer";
import { plotAltFromMeta, plotCaptionFromMeta } from "../plotLabels";

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
