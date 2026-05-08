import type { ContentBlock, DownloadArtifact, SSEEvent } from "./types";

type RawArtifact = {
  kind?: unknown;
  title?: unknown;
  filename?: unknown;
  url?: unknown;
  path?: unknown;
  row_count?: unknown;
  rowCount?: unknown;
};

function basename(value: string): string {
  return (value.split("/").pop() ?? value).split("?")[0] ?? value;
}

function normalizeArtifact(raw: unknown, groupLabel?: string): DownloadArtifact | null {
  if (!raw || typeof raw !== "object") return null;
  const a = raw as RawArtifact;
  if (a.kind !== "csv") return null;

  const source =
    typeof a.filename === "string"
      ? a.filename
      : typeof a.path === "string"
        ? a.path
        : typeof a.url === "string"
          ? a.url
          : "";
  const filename = basename(source);
  if (!filename.endsWith(".csv")) return null;
  const url =
    typeof a.url === "string" && a.url.startsWith("/api/")
      ? a.url
      : `/api/analysis-artifacts/${encodeURIComponent(filename)}`;
  const rowRaw = typeof a.rowCount === "number" ? a.rowCount : a.row_count;
  return {
    kind: "csv",
    title: typeof a.title === "string" && a.title.trim() ? a.title : "Flow data CSV",
    filename,
    url,
    ...(typeof rowRaw === "number" && Number.isFinite(rowRaw)
      ? { rowCount: rowRaw }
      : {}),
    ...(groupLabel ? { groupLabel } : {}),
  };
}

function artifactsFromResult(result: {
  download_artifacts?: unknown;
  meters?: Array<{ serial_number?: string; download_artifacts?: unknown }>;
}): DownloadArtifact[] {
  const out: DownloadArtifact[] = [];
  if (Array.isArray(result.meters) && result.meters.length > 0) {
    for (const meter of result.meters) {
      const serial = typeof meter.serial_number === "string" ? meter.serial_number : undefined;
      const artifacts = Array.isArray(meter.download_artifacts)
        ? meter.download_artifacts
        : [];
      for (const artifact of artifacts) {
        const normalized = normalizeArtifact(artifact, serial);
        if (normalized) out.push(normalized);
      }
    }
    return out;
  }

  const artifacts = Array.isArray(result.download_artifacts)
    ? result.download_artifacts
    : [];
  for (const artifact of artifacts) {
    const normalized = normalizeArtifact(artifact);
    if (normalized) out.push(normalized);
  }
  return out;
}

export function artifactsFromEvent(event: SSEEvent): DownloadArtifact[] {
  return artifactsFromResult(event);
}

export function extractDownloadArtifacts(content: string | ContentBlock[]): DownloadArtifact[] {
  if (typeof content === "string") return [];
  const out: DownloadArtifact[] = [];
  for (const block of content) {
    if (block.type !== "tool_result" || !block.content) continue;
    try {
      const result = JSON.parse(block.content) as {
        download_artifacts?: unknown;
        meters?: Array<{ serial_number?: string; download_artifacts?: unknown }>;
      };
      out.push(...artifactsFromResult(result));
    } catch {
      /* skip malformed tool JSON */
    }
  }
  return out;
}
