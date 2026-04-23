import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, ContentBlock, PlotAttachment } from "../types";
import { rebuildStepsFromStoredEvents, type TurnActivityStep } from "../turnActivity";
import PlotImage from "./PlotImage";
import TurnActivityTimeline from "./TurnActivityTimeline";

function extractText(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text!)
    .join("");
}

function isToolResultRow(content: string | ContentBlock[]): boolean {
  if (typeof content === "string") return false;
  return content.length > 0 && content[0]?.type === "tool_result";
}

function turnActivityFromMessage(
  content: string | ContentBlock[]
): TurnActivityStep[] | null {
  if (typeof content === "string") return null;
  const block = content.find(
    (b) => b.type === "turn_activity" && Array.isArray((b as ContentBlock).events)
  ) as ContentBlock | undefined;
  if (!block?.events?.length) return null;
  return rebuildStepsFromStoredEvents(
    block.events as Array<Record<string, unknown>>
  );
}

function rewritePlotPaths(text: string): string {
  return text.replace(
    /!\[([^\]]*)\]\(([^)]*\.png)\)/g,
    (_match, alt, src) => {
      const filename = src.split("/").pop() ?? src;
      return `![${alt}](/api/plots/${filename})`;
    },
  );
}

/** Remove markdown image lines so we do not double-render or hit wrong URLs from the LLM. */
function stripMarkdownPngImages(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\([^)]*\.png\)/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

interface MessageBubbleProps {
  message: Message;
  /** Plots to show after this assistant message (from prior ``tool_result`` rows). */
  plots?: PlotAttachment[];
}

export default function MessageBubble({ message, plots }: MessageBubbleProps) {
  if (isToolResultRow(message.content)) return null;

  const historyTurnActivity = useMemo(
    () =>
      message.role === "assistant"
        ? turnActivityFromMessage(message.content)
        : null,
    [message.role, message.content]
  );

  const rawText = extractText(message.content);
  const trimmed = rawText.trim();
  const isUser = message.role === "user";
  const hasPlots = !!(plots && plots.length > 0);
  const hasHistoryActivity =
    !!(historyTurnActivity && historyTurnActivity.length > 0);

  // User bubbles need text. Assistant bubbles need text, plots, and/or persisted turn activity.
  if (isUser && !trimmed) return null;
  if (!isUser && !trimmed && !hasPlots && !hasHistoryActivity) return null;

  // When tool_result provides plot_paths, render images only from those URLs — not from
  // markdown (the model often echoes a different timestamp than int(timestamps[0]) in filenames).
  const text = isUser
    ? rawText
    : !hasPlots
      ? rewritePlotPaths(rawText)
      : trimmed
        ? stripMarkdownPngImages(rawText)
        : "";

  // Assistant: only show the markdown/plot card when there is content inside it
  // (turn-only timeline with no text needs no empty card).
  const showAssistantBubble = isUser || Boolean(trimmed) || hasPlots;

  const assistantMarkdownBlock =
    !isUser && text ? (
      <div className="prose prose-base max-w-none min-w-0 break-words prose-p:my-2 prose-p:leading-relaxed prose-headings:text-brand-900 prose-a:break-words prose-a:text-brand-500 prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm dark:prose-invert dark:prose-headings:text-brand-900 [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_table]:block [&_table]:overflow-x-auto [&_img]:max-w-full sm:prose-sm sm:prose-p:my-1">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            img: ({ src, alt }) => (
              <PlotImage
                src={src ?? ""}
                alt={alt ?? undefined}
                className="w-full rounded-lg shadow-sm"
              />
            ),
          }}
        >
          {text}
        </ReactMarkdown>
      </div>
    ) : null;

  const assistantPlotsBlock =
    !isUser && plots?.length ? (
      <>
        {plots.map((p) => (
          <PlotImage
            key={p.src}
            src={p.src}
            alt="Flow analysis plot"
            title={p.title}
            plotTimezone={p.plotTimezone}
            plotType={p.plotType}
            className={`w-full rounded-lg shadow-sm ${text ? "mt-3" : ""}`}
          />
        ))}
      </>
    ) : null;

  if (isUser) {
    if (!showAssistantBubble) return null;
    return (
      <div className="flex flex-col gap-2 items-end">
        <div className="flex w-full justify-end">
          <div className="max-w-[min(92%,28rem)] min-w-0 overflow-hidden rounded-2xl bg-brand-700 px-4 py-3.5 text-white sm:max-w-[75%] sm:py-3">
            <p className="whitespace-pre-wrap break-words text-base leading-relaxed sm:text-sm">
              {text}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (hasHistoryActivity) {
    return (
      <div className="flex flex-col gap-2 items-start">
        <TurnActivityTimeline
          steps={historyTurnActivity ?? []}
          active={false}
        />
        {showAssistantBubble ? (
          <div className="flex w-full justify-start">
            <div className="max-w-[min(92%,28rem)] min-w-0 overflow-hidden rounded-2xl border border-brand-border bg-white px-4 py-3.5 text-brand-900 dark:border-brand-border dark:bg-brand-50 sm:max-w-[75%] sm:py-3">
              {assistantMarkdownBlock}
              {assistantPlotsBlock}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  if (!showAssistantBubble) return null;

  return (
    <div className="flex flex-col gap-2 items-start">
      <div className="flex w-full justify-start">
        <div className="max-w-[min(92%,28rem)] min-w-0 overflow-hidden rounded-2xl border border-brand-border bg-white px-4 py-3.5 text-brand-900 dark:border-brand-border dark:bg-brand-50 sm:max-w-[75%] sm:py-3">
          {assistantMarkdownBlock}
          {assistantPlotsBlock}
        </div>
      </div>
    </div>
  );
}
