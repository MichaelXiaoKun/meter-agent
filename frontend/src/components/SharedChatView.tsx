import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { loadPublicShare } from "../api";
import type { Message, PlotAttachment } from "../types";
import { stripTurnActivityBlocks } from "../utils/messageStrip";
import { exportTranscriptToPdf } from "../utils/pdfExport";
import { extractPlotAttachments } from "./plotAttachments";
import AnimatedMessageBubble from "./AnimatedMessageBubble";
import BluebotWordmarkLogo from "./BluebotWordmarkLogo";
import WelcomeBluebotLogo from "./WelcomeBluebotLogo";

function buildPlotsByIndex(messages: Message[]): Map<number, PlotAttachment[]> {
  const plotsByIndex = new Map<number, PlotAttachment[]>();
  let queued: PlotAttachment[] = [];
  messages.forEach((msg, i) => {
    const next = extractPlotAttachments(msg.content);
    if (next.length > 0) {
      queued.push(...next);
    }
    if (msg.role === "assistant" && queued.length > 0) {
      plotsByIndex.set(i, [...queued]);
      queued = [];
    }
  });
  return plotsByIndex;
}

function openAppHome() {
  const { origin, pathname } = window.location;
  window.location.href = `${origin}${pathname}#/login`;
}

export default function SharedChatView({ token }: { token: string }) {
  const [status, setStatus] = useState<"loading" | "error" | "ready">("loading");
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [rawMessages, setRawMessages] = useState<Message[]>([]);
  const [exporting, setExporting] = useState(false);
  const mainId = useId();

  const displayMessages = useMemo(
    () => stripTurnActivityBlocks(rawMessages),
    [rawMessages],
  );
  const plotsByIndex = useMemo(
    () => buildPlotsByIndex(displayMessages),
    [displayMessages],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setStatus("loading");
      setError(null);
      try {
        const d = await loadPublicShare(token);
        if (cancelled) return;
        setTitle(d.title || "Shared chat");
        setRawMessages(d.messages);
        setStatus("ready");
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const onExportPdf = useCallback(async () => {
    if (exporting || displayMessages.length === 0) return;
    setExporting(true);
    try {
      await exportTranscriptToPdf({
        messages: displayMessages,
        title: title || "Shared chat",
      });
    } finally {
      setExporting(false);
    }
  }, [displayMessages, exporting, title]);

  if (status === "loading") {
    return (
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-3 bg-brand-50 text-brand-muted">
        <WelcomeBluebotLogo
          size={64}
          mood="loading"
          interactive={false}
          sleepAfterMs={null}
          className="opacity-80"
        />
        <p className="text-sm">Loading shared conversation…</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-4 bg-brand-50 px-4 text-center">
        <p className="text-sm text-brand-900">{error ?? "Not found"}</p>
        <button
          type="button"
          onClick={openAppHome}
          className="rounded-lg border border-brand-border bg-white px-4 py-2 text-sm font-medium text-brand-700 shadow-sm hover:bg-brand-50"
        >
          Open bluebot Assistant
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] max-h-[100dvh] min-h-0 flex-col overflow-hidden bg-brand-50 text-brand-900">
      <header
        className="shrink-0 border-b border-brand-border/90 bg-gradient-to-b from-white to-brand-50/40 shadow-[0_1px_0_0_rgba(15,23,42,0.04)] dark:from-brand-100 dark:to-brand-50 dark:shadow-[0_1px_0_0_rgba(0,0,0,0.2)]"
        style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top, 0px))" }}
      >
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-4 py-3 sm:px-6">
          <div className="min-w-0 flex items-center gap-2">
            <BluebotWordmarkLogo className="h-5 w-auto shrink-0 text-brand-700 dark:text-brand-muted" />
            <h1 className="truncate text-sm font-semibold text-brand-900 sm:text-base">
              {title}
            </h1>
          </div>
          <button
            type="button"
            onClick={onExportPdf}
            disabled={exporting}
            className="shrink-0 rounded-lg border border-brand-border bg-white px-3 py-1.5 text-xs font-medium text-brand-800 shadow-sm hover:bg-brand-50 dark:border-brand-border dark:bg-brand-100 dark:hover:bg-white/10 sm:text-sm"
          >
            {exporting ? "…" : "Export PDF"}
          </button>
        </div>
      </header>
      <main
        id={mainId}
        className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain [-webkit-overflow-scrolling:touch] px-4 py-4 sm:px-6"
      >
        <div className="mx-auto w-full max-w-3xl space-y-3 pb-8">
          <p className="text-xs text-brand-muted">
            This is a read-only snapshot.{" "}
            <button
              type="button"
              onClick={openAppHome}
              className="font-medium text-brand-600 underline decoration-brand-500/30 hover:decoration-brand-500"
            >
              Sign in
            </button>{" "}
            to start your own chat.
          </p>
          {displayMessages.map((msg, i) => (
            <AnimatedMessageBubble
              key={i}
              message={msg}
              plots={plotsByIndex.get(i)}
              transcript={displayMessages}
              messageIndex={i}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
