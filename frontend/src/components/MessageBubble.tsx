import { useMemo, useState } from "react";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-1 opacity-0 group-hover/bubble:opacity-100 transition-opacity duration-150 mt-0.5">
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch { /* ignore */ }
        }}
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-brand-muted hover:text-brand-900 hover:bg-brand-100 dark:hover:bg-white/10 dark:hover:text-brand-900 transition-colors"
        title="Copy message"
      >
        {copied ? (
          <>
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
            Copied
          </>
        ) : (
          <>
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            Copy
          </>
        )}
      </button>
    </div>
  );
}
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { DownloadArtifact, Message, ContentBlock, PlotAttachment, SSEEvent } from "../types";
import {
  rebuildStepsFromStoredEvents,
  splitActivityAtFirstTool,
  splitTurnActivityAroundStreamBody,
  type TurnActivityStep,
} from "../turnActivity";
import PlotImage, { PlotGrouped } from "./PlotImage";
import ArtifactLinks from "./ArtifactLinks";
import TurnActivityTimeline from "./TurnActivityTimeline";
import ConfigConfirmationCard from "./ConfigConfirmationCard";

type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;
type ToastFn = (a: {
  kind: "success" | "error";
  title: string;
  message?: string;
}) => void;

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

function hasTurnActivityBlock(content: string | ContentBlock[]): boolean {
  if (typeof content === "string") return false;
  return content.some(
    (b) => b.type === "turn_activity" && Array.isArray((b as ContentBlock).events)
  );
}

function hasToolUseBlock(content: string | ContentBlock[]): boolean {
  if (typeof content === "string") return false;
  return content.some((b) => b.type === "tool_use");
}

/** True if a later assistant in this turn (before the next real user) carries ``turn_activity``. */
function preambleFoldedIntoLaterAssistant(
  transcript: Message[],
  messageIndex: number
): boolean {
  for (let j = messageIndex + 1; j < transcript.length; j++) {
    const m = transcript[j];
    if (m.role === "user" && !isToolResultRow(m.content)) return false;
    if (m.role === "assistant" && hasTurnActivityBlock(m.content)) return true;
  }
  return false;
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

function configWorkflowFromMessage(content: string | ContentBlock[]): ConfigWorkflow | null {
  const workflows = configWorkflowsFromContent(content);
  return workflows[workflows.length - 1] ?? null;
}

function configWorkflowsFromContent(
  content: string | ContentBlock[]
): ConfigWorkflow[] {
  if (typeof content === "string") return [];
  const block = content.find(
    (b) => b.type === "turn_activity" && Array.isArray((b as ContentBlock).events)
  ) as ContentBlock | undefined;
  const events = block?.events ?? [];
  const workflows: ConfigWorkflow[] = [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const workflow = (events[i] as unknown as SSEEvent | undefined)?.config_workflow;
    if (workflow?.action_id) workflows.unshift(workflow);
  }
  return workflows;
}

function latestConfigWorkflowForAction(
  workflow: ConfigWorkflow | null,
  transcript: Message[] | undefined,
  messageIndex: number | undefined,
  liveEvents?: SSEEvent[],
): ConfigWorkflow | null {
  if (!workflow?.action_id) return workflow;
  let latest = workflow;
  if (transcript != null && messageIndex != null) {
    for (let i = messageIndex + 1; i < transcript.length; i += 1) {
      const msg = transcript[i];
      if (!msg || msg.role !== "assistant") continue;
      for (const candidate of configWorkflowsFromContent(msg.content)) {
        if (candidate.action_id === workflow.action_id) {
          latest = { ...latest, ...candidate };
        }
      }
    }
  }
  for (const ev of liveEvents ?? []) {
    const candidate = ev.config_workflow;
    if (candidate?.action_id === workflow.action_id) {
      latest = { ...latest, ...candidate };
    }
  }
  return latest;
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

function contentBlocks(content: string | ContentBlock[]): ContentBlock[] {
  if (typeof content === "string") return [{ type: "text", text: content }];
  return Array.isArray(content) ? content : [];
}

/** Assistant ``text`` blocks strictly before the first ``tool_use`` in that message. */
function assistantTextBeforeFirstTool(content: string | ContentBlock[]): string {
  const parts: string[] = [];
  for (const b of contentBlocks(content)) {
    if (!b || typeof b !== "object") continue;
    if (b.type === "text" && b.text) parts.push(b.text);
    if (b.type === "tool_use") break;
  }
  return parts.join("");
}

/**
 * Preamble streamed before any tool in this user turn — it is stored on earlier
 * assistant rows while ``turn_activity`` is attached to the final assistant message.
 */
function extractPreToolMarkdownBeforeMessage(
  transcript: Message[],
  assistantIndex: number
): string {
  if (assistantIndex <= 0) return "";
  let userIdx = -1;
  for (let j = assistantIndex - 1; j >= 0; j--) {
    const m = transcript[j];
    if (m?.role === "user" && !isToolResultRow(m.content)) {
      userIdx = j;
      break;
    }
  }
  if (userIdx < 0) return "";
  const chunks: string[] = [];
  for (let j = userIdx + 1; j < assistantIndex; j++) {
    const m = transcript[j];
    if (!m) continue;
    if (m.role === "user" && isToolResultRow(m.content)) continue;
    if (m.role !== "assistant") continue;
    chunks.push(assistantTextBeforeFirstTool(m.content));
  }
  return chunks.join("");
}

interface MessageBubbleProps {
  message: Message;
  /** Plots to show after this assistant message (from prior ``tool_result`` rows). */
  plots?: PlotAttachment[];
  /** Download artifacts to show after this assistant message. */
  artifacts?: DownloadArtifact[];
  /** Same-conversation messages (server order) — enables pre-tool / tool / post-tool layout. */
  transcript?: Message[];
  messageIndex?: number;
  onConfirmConfig?: (workflow: ConfigWorkflow) => void;
  onCancelConfig?: (workflow: ConfigWorkflow) => void;
  onTypeOtherConfig?: (workflow: ConfigWorkflow) => void;
  configActionsDisabled?: boolean;
  liveConfigEvents?: SSEEvent[];
  accessToken?: string | null;
  anthropicApiKey?: string | null;
  onToast?: ToastFn;
}

export default function MessageBubble({
  message,
  plots,
  artifacts,
  transcript,
  messageIndex,
  onConfirmConfig,
  onCancelConfig,
  onTypeOtherConfig,
  configActionsDisabled,
  liveConfigEvents,
  accessToken,
  anthropicApiKey,
  onToast,
}: MessageBubbleProps) {
  if (isToolResultRow(message.content)) return null;

  // Preamble for this tool turn is replayed on the final assistant row (with ``turn_activity``).
  if (
    message.role === "assistant" &&
    transcript != null &&
    messageIndex != null &&
    hasToolUseBlock(message.content) &&
    !hasTurnActivityBlock(message.content) &&
    preambleFoldedIntoLaterAssistant(transcript, messageIndex)
  ) {
    return null;
  }

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
  const hasArtifacts = !!(artifacts && artifacts.length > 0);
  const hasHistoryActivity =
    !!(historyTurnActivity && historyTurnActivity.length > 0);
  const configWorkflow = useMemo(
    () =>
      message.role === "assistant"
        ? latestConfigWorkflowForAction(
            configWorkflowFromMessage(message.content),
            transcript,
            messageIndex,
            liveConfigEvents,
          )
        : null,
    [message.role, message.content, transcript, messageIndex, liveConfigEvents]
  );

  // User bubbles need text. Assistant bubbles need text, artifacts, and/or persisted turn activity.
  if (isUser && !trimmed) return null;
  if (!isUser && !trimmed && !hasPlots && !hasArtifacts && !hasHistoryActivity) return null;

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
  const showAssistantBubble = isUser || Boolean(trimmed) || hasPlots || hasArtifacts;

  const hasBodyForActivitySplit =
    message.role === "assistant" && (Boolean(trimmed) || hasPlots || hasArtifacts);
  const { above: activityAboveBody, below: activityBelowBody } = useMemo(
    () =>
      splitTurnActivityAroundStreamBody(
        historyTurnActivity ?? [],
        hasBodyForActivitySplit
      ),
    [historyTurnActivity, hasBodyForActivitySplit]
  );

  const { beforeTools: historyBeforeTools, fromFirstTool: historyFromFirstTool } =
    useMemo(
      () => splitActivityAtFirstTool(activityAboveBody),
      [activityAboveBody]
    );

  const preToolRaw = useMemo(() => {
    if (message.role !== "assistant" || transcript == null || messageIndex == null) {
      return "";
    }
    return extractPreToolMarkdownBeforeMessage(transcript, messageIndex);
  }, [message.role, transcript, messageIndex]);

  const preToolDisplay = useMemo(() => {
    const t = preToolRaw.trim();
    if (!t) return "";
    return rewritePlotPaths(preToolRaw);
  }, [preToolRaw]);

  const showPostBubble = Boolean(trimmed) || hasPlots || hasArtifacts;

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
      <PlotGrouped
        plots={plots}
        className={`w-full rounded-lg shadow-sm ${text ? "mt-3" : ""}`}
      />
    ) : null;

  const assistantArtifactsBlock =
    !isUser && artifacts?.length ? (
      <ArtifactLinks
        artifacts={artifacts}
        accessToken={accessToken}
        anthropicApiKey={anthropicApiKey}
        onToast={onToast}
        className={text || hasPlots ? "mt-3" : ""}
      />
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
    // Match live ChatView: pre-tool strip → first reply → tool strip → post-tool reply → done.
    const proseCard =
      "max-w-2xl min-w-0 w-full py-1";
    const proseInner =
      "prose prose-base max-w-none min-w-0 break-words prose-p:my-2 prose-p:leading-relaxed prose-headings:text-brand-900 prose-a:break-words prose-a:text-brand-500 prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm dark:prose-invert dark:prose-headings:text-brand-900 [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_table]:block [&_table]:overflow-x-auto [&_img]:max-w-full sm:prose-sm sm:prose-p:my-1";
    const mdComponents = {
      img: ({ src, alt }: { src?: string; alt?: string }) => (
        <PlotImage
          src={src ?? ""}
          alt={alt ?? undefined}
          className="w-full rounded-lg shadow-sm"
        />
      ),
    };
    return (
      <div className="flex flex-col gap-2 items-start">
        {historyBeforeTools.length > 0 ? (
          <TurnActivityTimeline steps={historyBeforeTools} active={false} />
        ) : null}
        {preToolDisplay.trim() ? (
          <div className="flex w-full justify-start">
            <div className={proseCard}>
              <div className={proseInner}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {preToolDisplay}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        ) : null}
        {historyFromFirstTool.length > 0 ? (
          <TurnActivityTimeline steps={historyFromFirstTool} active={false} />
        ) : null}
        {showPostBubble ? (
          <div className="group/bubble flex flex-col">
            <div className={proseCard}>
              {assistantMarkdownBlock}
              {assistantPlotsBlock}
              {assistantArtifactsBlock}
            </div>
            {text && <CopyButton text={text} />}
          </div>
        ) : null}
        {activityBelowBody.length > 0 ? (
          <TurnActivityTimeline
            steps={activityBelowBody}
            active={false}
            announce={false}
          />
        ) : null}
        {configWorkflow?.status === "pending_confirmation" ||
        configWorkflow?.status === "superseded" ? (
          <ConfigConfirmationCard
            workflow={configWorkflow}
            disabled={configActionsDisabled}
            onConfirm={onConfirmConfig}
            onCancel={onCancelConfig}
            onTypeOther={onTypeOtherConfig}
          />
        ) : null}
      </div>
    );
  }

  if (!showAssistantBubble) return null;

  return (
    <div className="flex flex-col gap-2 items-start">
      <div className="group/bubble flex flex-col">
        <div className="max-w-2xl min-w-0 w-full py-1">
          {assistantMarkdownBlock}
          {assistantPlotsBlock}
          {assistantArtifactsBlock}
        </div>
        {text && <CopyButton text={text} />}
      </div>
    </div>
  );
}
