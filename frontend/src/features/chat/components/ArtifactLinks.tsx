import { useState } from "react";
import type { DownloadArtifact } from "../../../core/types";
import { downloadArtifact } from "../../../api/client";

type ToastFn = (a: {
  kind: "success" | "error";
  title: string;
  message?: string;
}) => void;

function FileIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M8 13h8" />
      <path d="M8 17h8" />
      <path d="M8 9h2" />
    </svg>
  );
}

function DownloadIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M7 10l5 5 5-5" />
      <path d="M12 15V3" />
    </svg>
  );
}

function formatRows(rowCount?: number): string | null {
  if (typeof rowCount !== "number" || !Number.isFinite(rowCount)) return null;
  return `${new Intl.NumberFormat().format(rowCount)} rows`;
}

function dedupeArtifacts(artifacts: DownloadArtifact[]): DownloadArtifact[] {
  const seen = new Set<string>();
  const out: DownloadArtifact[] = [];
  for (const artifact of artifacts) {
    const key = `${artifact.groupLabel ?? ""}:${artifact.filename}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(artifact);
  }
  return out;
}

export default function ArtifactLinks({
  artifacts,
  accessToken,
  anthropicApiKey,
  onToast,
  className = "",
}: {
  artifacts: DownloadArtifact[];
  accessToken?: string | null;
  anthropicApiKey?: string | null;
  onToast?: ToastFn;
  className?: string;
}) {
  const [activeFilename, setActiveFilename] = useState<string | null>(null);
  const visible = dedupeArtifacts(artifacts).filter((a) => a.kind === "csv");
  if (!visible.length) return null;

  async function handleDownload(artifact: DownloadArtifact) {
    if (!accessToken) {
      onToast?.({
        kind: "error",
        title: "Sign in required",
        message: "CSV artifacts require an authenticated download request.",
      });
      return;
    }
    setActiveFilename(artifact.filename);
    try {
      await downloadArtifact(
        artifact.url,
        artifact.filename,
        accessToken,
        anthropicApiKey,
      );
      onToast?.({
        kind: "success",
        title: "CSV download started",
        message: artifact.filename,
      });
    } catch (e) {
      onToast?.({
        kind: "error",
        title: "Could not download CSV",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setActiveFilename(null);
    }
  }

  return (
    <div className={`flex w-full flex-col gap-2 ${className}`}>
      {visible.map((artifact) => {
        const rows = formatRows(artifact.rowCount);
        const loading = activeFilename === artifact.filename;
        return (
          <button
            key={`${artifact.groupLabel ?? "single"}:${artifact.filename}`}
            type="button"
            onClick={() => void handleDownload(artifact)}
            disabled={loading}
            className="group flex w-full max-w-2xl items-center gap-3 rounded-lg border border-brand-border bg-white px-3 py-2.5 text-left shadow-sm transition-colors hover:border-brand-400 hover:bg-brand-50 disabled:cursor-wait disabled:opacity-75 dark:bg-brand-100 dark:hover:bg-white/10"
            title={artifact.filename}
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand-100 text-brand-700 dark:bg-white/10 dark:text-brand-muted">
              <FileIcon className="h-5 w-5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-brand-900">
                Download CSV
              </span>
              <span className="block truncate text-xs text-brand-muted">
                {[artifact.groupLabel, rows, artifact.filename]
                  .filter(Boolean)
                  .join(" - ")}
              </span>
            </span>
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-brand-600 transition-colors group-hover:bg-brand-100 group-hover:text-brand-800 dark:text-brand-muted dark:group-hover:bg-white/10">
              {loading ? (
                <svg
                  className="h-4 w-4 animate-spin"
                  fill="none"
                  viewBox="0 0 24 24"
                  aria-hidden
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
              ) : (
                <DownloadIcon className="h-4 w-4" />
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
