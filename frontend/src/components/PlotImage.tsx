import { useState } from "react";
import ImageViewer from "./ImageViewer";

interface PlotImageProps {
  src: string;
  alt?: string;
  className?: string;
}

export default function PlotImage({ src, alt, className }: PlotImageProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <img
        src={src}
        alt={alt ?? "Plot"}
        className={`cursor-zoom-in ${className ?? ""}`}
        onClick={() => setOpen(true)}
      />
      {open && (
        <ImageViewer src={src} alt={alt} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
