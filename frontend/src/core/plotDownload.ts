export function plotBasename(src: string): string {
  try {
    const u = new URL(src, window.location.origin);
    const seg = u.pathname.split("/").filter(Boolean).pop();
    return seg && /\.png$/i.test(seg) ? seg : "plot.png";
  } catch {
    const seg = src.split("/").pop();
    return seg && /\.png$/i.test(seg) ? seg : "plot.png";
  }
}

export async function downloadPlotImage(src: string): Promise<void> {
  const name = plotBasename(src);
  try {
    const res = await fetch(src);
    if (!res.ok) throw new Error(String(res.status));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch {
    window.open(src, "_blank", "noopener,noreferrer");
  }
}
