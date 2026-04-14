import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import type { AgentStatus } from "../hooks/useChat";
import MessageBubble, { extractPlotPaths } from "./MessageBubble";
import PlotImage from "./PlotImage";
import StatusIndicator from "./StatusIndicator";
import WelcomeCard from "./WelcomeCard";

interface ChatViewProps {
  messages: Message[];
  status: AgentStatus;
  streamingText: string;
  pendingPlots: string[];
  serverProcessing: boolean;
  onSend: (text: string) => void;
  disabled: boolean;
}

export default function ChatView({
  messages,
  status,
  streamingText,
  pendingPlots,
  serverProcessing,
  onSend,
  disabled,
}: ChatViewProps) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isProcessing = status.kind !== "idle" && status.kind !== "error";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingText, status]);

  function handleSubmit(text?: string) {
    const msg = text ?? input;
    if (!msg.trim() || disabled) return;
    onSend(msg);
    setInput("");
  }

  const hasMessages = messages.length > 0 || streamingText;

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
      {/* Header */}
      <div className="border-b border-brand-border bg-white/80 px-6 py-3 text-center backdrop-blur">
        <div className="flex items-center justify-center gap-2">
          <img
            src="/api/logo"
            alt="bluebot"
            className="h-8 w-8 rounded-lg object-cover shadow-sm"
          />
          <h1 className="text-lg font-bold tracking-tight text-brand-900">
            bluebot Assistant
          </h1>
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {hasMessages ? (
          <div className="mx-auto max-w-3xl space-y-3">
            {messages.map((msg, i) => (
              <MessageBubble
                key={i}
                message={msg}
                plotPaths={plotsByIndex.get(i)}
              />
            ))}

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
          <WelcomeCard onExampleClick={(text) => {
            setInput(text);
            requestAnimationFrame(() => {
              const el = inputRef.current;
              if (!el) return;
              el.focus();
              const start = text.indexOf("<serial number>");
              if (start !== -1) {
                el.setSelectionRange(start, start + "<serial number>".length);
              }
            });
          }} />
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
              className="flex gap-2"
            >
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about a meter or flow data..."
                disabled={isProcessing}
                className="flex-1 rounded-xl border-[1.5px] border-brand-border bg-white px-4 py-3 text-sm text-brand-900 outline-none transition-colors placeholder:text-brand-muted/60 focus:border-brand-500 focus:ring-3 focus:ring-brand-500/10 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={isProcessing || !input.trim()}
                className="rounded-xl bg-brand-700 px-5 py-3 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40"
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
