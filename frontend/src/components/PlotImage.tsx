import { useState } from "react";
import ImageViewer from "./ImageViewer";

interface PlotImageProps {
  src: string;
  alt?: string;
  className?: string;
}

export default function PlotImage({ src, alt, className }: PlotImageProps) {
  const [open, setOpen] = useState(false);
  const [loadError, setLoadError] = useState(false);

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

  return (
    <>
      <img
        src={src}
        alt={alt ?? "Plot"}
        className={`cursor-zoom-in ${className ?? ""}`}
        onClick={() => setOpen(true)}
        onError={() => setLoadError(true)}
      />
      {open && !loadError && (
        <ImageViewer src={src} alt={alt} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
