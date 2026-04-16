import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import type { AgentStatus } from "../hooks/useChat";
import MessageBubble, { extractPlotPaths } from "./MessageBubble";
import PlotImage from "./PlotImage";
import StatusIndicator from "./StatusIndicator";
import WelcomeCard from "./WelcomeCard";
import TurnActivityTimeline from "./TurnActivityTimeline";
import { TokenBudgetPopover } from "./TokenBudget";
import type { TurnActivityStep } from "../turnActivity";

interface ChatViewProps {
  messages: Message[];
  status: AgentStatus;
  streamingText: string;
  pendingPlots: string[];
  tokenUsage: { tokens: number; pct: number };
  /** True while fetching messages for the active conversation (empty transcript). */
  historyLoading: boolean;
  /** Server TPM guide (ITPM-style bar) — from GET /api/config. */
  tpmInputGuideTokens: number;
  /** Orchestrator process: sum of input tokens in last 60s (same API key). */
  tpmServerSliding60s: number;
  /** Full model context window (informational) — from GET /api/config. */
  modelContextWindowTokens: number;
  /** Input token target before compress — main context bar denominator. */
  maxInputTokensTarget: number;
  turnActivity: TurnActivityStep[];
  turnActivityActive: boolean;
  serverProcessing: boolean;
  onSend: (text: string) => void;
  onDismissAssistantError?: () => void;
  disabled: boolean;
}

export default function ChatView({
  messages,
  status,
  streamingText,
  pendingPlots,
  tokenUsage,
  historyLoading,
  tpmInputGuideTokens,
  tpmServerSliding60s,
  modelContextWindowTokens,
  maxInputTokensTarget,
  turnActivity,
  turnActivityActive,
  serverProcessing,
  onSend,
  onDismissAssistantError,
  disabled,
}: ChatViewProps) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isProcessing = status.kind !== "idle" && status.kind !== "error";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, status, turnActivity]);

  function handleSubmit(text?: string) {
    const msg = text ?? input;
    if (!msg.trim() || disabled) return;
    onSend(msg);
    setInput("");
  }

  // Keep the transcript + status visible while a turn is running even if messages were cleared
  // briefly (load effect, Strict Mode) — otherwise the whole pane becomes Welcome + idle.
  const statusActive =
    status.kind !== "idle" && status.kind !== "error";
  const hasMessages =
    messages.length > 0 ||
    !!streamingText ||
    statusActive ||
    status.kind === "error" ||
    turnActivity.length > 0;

  // Pair plot paths with assistant messages: collect from tool_result rows,
  // attach to the next assistant message (same logic as the Streamlit app).
  const plotsByIndex = new Map<number, string[]>();
  {
    let queued: string[] = [];
    messages.forEach((msg, i) => {
      const paths = extractPlotPaths(msg.content);
      if (paths.length > 0) {
        queued.push(...paths);
      }
      if (msg.role === "assistant" && queued.length > 0) {
        plotsByIndex.set(i, [...queued]);
        queued = [];
      }
    });
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header — aligned with chat column below */}
      <header className="shrink-0 border-b border-brand-border/90 bg-gradient-to-b from-white to-brand-50/40 shadow-[0_1px_0_0_rgba(15,23,42,0.04)] backdrop-blur-md">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3.5 sm:px-6">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm ring-1 ring-brand-border/70">
            <img
              src="/api/logo"
              alt=""
              className="h-9 w-9 rounded-lg object-cover"
              width={36}
              height={36}
            />
          </div>
          <div className="min-w-0 flex-1 text-left">
            <h1 className="text-base font-bold tracking-tight text-brand-900 sm:text-[1.0625rem]">
              bluebot Assistant
            </h1>
            <p className="mt-0.5 text-xs leading-snug text-brand-muted">
              Flow analysis, meter health, and pipe configuration — ask with a serial number.
            </p>
          </div>
        </div>
      </header>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {historyLoading && messages.length === 0 ? (
          <div className="mx-auto flex max-w-3xl flex-col items-center justify-center gap-3 py-16 text-brand-muted">
            <svg
              className="h-8 w-8 animate-spin text-brand-500"
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
            <p className="text-sm">Loading conversation…</p>
          </div>
        ) : hasMessages ? (
          <div className="mx-auto max-w-3xl space-y-3">
            {status.kind === "error" && (
              <div
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold text-red-950">
                      Assistant couldn&apos;t finish (Claude API)
                    </p>
                    <p className="mt-1.5 whitespace-pre-wrap break-words text-red-800/95">
                      {status.error}
                    </p>
                  </div>
                  {onDismissAssistantError && (
                    <button
                      type="button"
                      onClick={onDismissAssistantError}
                      className="shrink-0 rounded-lg border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-800 hover:bg-red-100"
                    >
                      Dismiss
                    </button>
                  )}
                </div>
              </div>
            )}
            {messages.map((msg, i) => (
              <MessageBubble
                key={i}
                message={msg}
                plotPaths={plotsByIndex.get(i)}
              />
            ))}

            {turnActivity.length > 0 && (
              <TurnActivityTimeline
                steps={turnActivity}
                active={turnActivityActive}
              />
            )}

            {streamingText && (
              <div className="flex justify-start">
                <div className="max-w-[75%] rounded-2xl border border-brand-border bg-white px-4 py-3 text-brand-900">
                  <div className="prose prose-sm max-w-none prose-p:my-1 prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                  </div>
                </div>
              </div>
            )}

            {/* Show plots arriving during streaming, before final message is persisted */}
            {pendingPlots.length > 0 && (
              <div className="flex justify-start">
                <div className="max-w-[75%] space-y-2">
                  {pendingPlots.map((src) => (
                    <PlotImage
                      key={src}
                      src={src}
                      alt="Flow analysis plot"
                      className="w-full rounded-lg border border-brand-border shadow-sm"
                    />
                  ))}
                </div>
              </div>
            )}

            <StatusIndicator status={status} />

            {serverProcessing && status.kind === "idle" && (
              <div className="flex items-center gap-2 rounded-lg bg-brand-100 px-3 py-2 text-sm text-brand-muted">
                <svg
                  className="h-4 w-4 animate-spin text-brand-500"
                  fill="none"
                  viewBox="0 0 24 24"
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
                Catching up — the server is still processing your request...
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        ) : (
          <WelcomeCard
            onCompose={(text) => {
              setInput(text);
              requestAnimationFrame(() => {
                const el = inputRef.current;
                if (!el) return;
                el.focus();
                el.select();
              });
            }}
          />
        )}
      </div>

      {/* Input */}
      <div className="border-t border-brand-border bg-white px-6 py-4">
        <div className="mx-auto max-w-3xl">
          {disabled ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-center text-sm text-amber-700">
              Enter your bluebot token in Settings to start chatting.
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSubmit();
              }}
              className="flex flex-wrap items-end gap-2"
            >
              <TokenBudgetPopover
                tokenUsage={tokenUsage}
                tpmPerMinuteGuide={tpmInputGuideTokens}
                tpmServerSliding60s={tpmServerSliding60s}
                modelContextMax={modelContextWindowTokens}
                inputBudgetTarget={maxInputTokensTarget}
              />
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about health, flow, or pipe setup (serial number)..."
                disabled={isProcessing}
                className="min-w-[12rem] flex-1 rounded-xl border-[1.5px] border-brand-border bg-white px-4 py-3 text-sm text-brand-900 outline-none transition-colors placeholder:text-brand-muted/60 focus:border-brand-500 focus:ring-3 focus:ring-brand-500/10 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={isProcessing || !input.trim()}
                className="shrink-0 rounded-xl bg-brand-700 px-5 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                Send
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
