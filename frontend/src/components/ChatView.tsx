import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { useMediaQuery } from "../hooks/useMediaQuery";

interface ChatViewProps {
  /**
   * ID of the currently selected conversation (``null`` while the welcome
   * screen is showing). When this changes we treat it as a "window switch"
   * and force the transcript back to the bottom so the user always lands on
   * the most recent message — same behaviour as iMessage / Slack switching
   * channels.
   */
  conversationId: string | null;
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
  /**
   * Mobile (narrow) layout: no left rail — bluebot logo in the header opens the drawer.
   */
  narrowNav?: {
    onOpenSidebar: () => void;
  };
}

export default function ChatView({
  conversationId,
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
  narrowNav,
}: ChatViewProps) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollElRef = useRef<HTMLDivElement | null>(null);
  const scrollListenerCleanupRef = useRef<(() => void) | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  /**
   * Set to ``true`` for the duration of a user-initiated smooth scroll
   * (``Jump to latest`` click). While true the streaming auto-scroll effect
   * and the plot-image ``ResizeObserver`` are short-circuited so they don't
   * teleport past the in-flight animation. Cleared on the native
   * ``scrollend`` event (modern browsers) or by a 700 ms safety timeout for
   * engines that don't fire it yet.
   */
  const smoothScrollInFlightRef = useRef(false);
  const smoothScrollTimeoutRef = useRef<number | null>(null);

  /**
   * Scroll the transcript container all the way to the end of its
   * *scrollable area* — not just to ``bottomRef`` via ``scrollIntoView``.
   *
   * The composer is rendered as a ``position: sticky`` sibling at the bottom
   * of the same scroll container; ``scrollIntoView({ block: "end" })`` would
   * align ``bottomRef`` to the container's bottom edge, where the sticky
   * composer is also pinned, so the last lines of the response (and the
   * bottom edge of plot images) ended up tucked behind the composer.
   *
   * Setting ``scrollTop = scrollHeight`` lands at the true end of scrollable
   * content. The transcript's ``pb-36/sm:pb-40`` bottom padding then keeps
   * the last message and any attached plots clear of the sticky composer.
   */
  const scrollContainerToBottom = (smooth = false) => {
    const el = scrollElRef.current;
    if (!el) return;
    if (smooth) {
      smoothScrollInFlightRef.current = true;
      if (smoothScrollTimeoutRef.current != null) {
        window.clearTimeout(smoothScrollTimeoutRef.current);
      }
      smoothScrollTimeoutRef.current = window.setTimeout(() => {
        smoothScrollInFlightRef.current = false;
        smoothScrollTimeoutRef.current = null;
      }, 700);
    }
    el.scrollTo({
      top: el.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  };
  /**
   * Sticky-bottom autoscroll. ``true`` → the viewport is "following the latest
   * output" and we should snap to bottom whenever new content arrives. As soon
   * as the user scrolls up by more than ``STICK_THRESHOLD_PX`` we flip to
   * ``false`` so they can read history without being yanked back. We mirror the
   * value into a ref so the autoscroll effect can read the latest value
   * without re-running the listener-attach effect.
   */
  const STICK_THRESHOLD_PX = 80;
  const stickToBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const isProcessing = status.kind !== "idle" && status.kind !== "error";
  /** Same breakpoint as App sidebar — pin welcome composer to bottom on phones/tablets. */
  const welcomeComposerAtBottom = useMediaQuery("(max-width: 1023px)");

  /**
   * Callback ref for the scroll container. The container is conditionally
   * rendered (welcome screen vs. transcript, and unmounted while
   * ``historyLoading``), and on conversation switch it remounts. A callback
   * ref guarantees the scroll listener is attached the moment the node is in
   * the DOM and detached when it leaves — no effect-deps dance required.
   */
  const setScrollEl = (el: HTMLDivElement | null) => {
    scrollListenerCleanupRef.current?.();
    scrollListenerCleanupRef.current = null;
    scrollElRef.current = el;
    if (!el) return;
    const onScroll = () => {
      // Don't let an in-flight programmatic smooth scroll flip the sticky
      // state mid-animation — the user explicitly asked to land at the
      // bottom, and the intermediate distances would otherwise hide the
      // pill and back again, then mark them as "scrolled away".
      if (smoothScrollInFlightRef.current) return;
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const near = distanceFromBottom < STICK_THRESHOLD_PX;
      stickToBottomRef.current = near;
      setShowJumpToLatest((prev) => (prev === near ? !near : prev));
    };
    // ``scrollend`` is the precise signal that a smooth scroll has finished
    // (both user-initiated wheel/touch and ``scrollTo({behavior:"smooth"})``).
    // Supported in Chrome 114+, Firefox 109+, Safari 18.2+. The 700 ms
    // timeout in ``scrollContainerToBottom`` covers older engines.
    const onScrollEnd = () => {
      smoothScrollInFlightRef.current = false;
      if (smoothScrollTimeoutRef.current != null) {
        window.clearTimeout(smoothScrollTimeoutRef.current);
        smoothScrollTimeoutRef.current = null;
      }
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const near = distanceFromBottom < STICK_THRESHOLD_PX;
      stickToBottomRef.current = near;
      setShowJumpToLatest((prev) => (prev === near ? !near : prev));
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("scrollend", onScrollEnd);
    scrollListenerCleanupRef.current = () => {
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("scrollend", onScrollEnd);
    };
  };

  /**
   * Callback ref for the inner transcript div. Plot images load
   * asynchronously and grow the transcript height after our data-change
   * effect has already run; ``ResizeObserver`` fires on any growth (image
   * load, async render, font metrics) so we re-snap to bottom without having
   * to wire ``onLoad`` through every ``PlotImage``.
   */
  const setTranscriptInnerEl = (el: HTMLDivElement | null) => {
    resizeObserverRef.current?.disconnect();
    resizeObserverRef.current = null;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      // Don't snap during an in-flight smooth scroll — an instant snap mid-
      // animation feels like the page glitched, and is the main reason
      // ``Jump to latest`` looked like it teleported when plot images were
      // still finishing decode.
      if (smoothScrollInFlightRef.current) return;
      if (stickToBottomRef.current) {
        scrollContainerToBottom();
      }
    });
    ro.observe(el);
    resizeObserverRef.current = ro;
  };

  // Auto-scroll on new content — but ONLY when we're currently sticky.
  // ``"auto"`` (instant) avoids the smooth-scroll fight that used to make the
  // viewport feel "trapped" while text was streaming in chunks.
  useEffect(() => {
    if (smoothScrollInFlightRef.current) return;
    if (!stickToBottomRef.current) return;
    scrollContainerToBottom();
  }, [messages, streamingText, status, turnActivity, pendingPlots]);

  /**
   * Window-switch behaviour: whenever the active conversation changes, force
   * the transcript back to "stuck at bottom" so the user always lands on the
   * most recent message — never frozen partway through some prior scroll
   * position. We use ``useLayoutEffect`` so the snap happens before the
   * browser paints the newly-rendered transcript, avoiding a one-frame flash
   * of mid-history content.
   *
   * The actual ``scrollIntoView`` is queued in ``requestAnimationFrame``
   * because on conversation switch the new messages arrive asynchronously
   * (``historyLoading`` flips true → false) and the scroll container itself
   * remounts; rAF lets the new DOM settle so the bottom anchor is in its
   * final position before we snap to it.
   */
  useLayoutEffect(() => {
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollContainerToBottom();
    const raf = requestAnimationFrame(() => scrollContainerToBottom());
    return () => cancelAnimationFrame(raf);
  }, [conversationId, historyLoading]);

  useEffect(
    () => () => {
      if (smoothScrollTimeoutRef.current != null) {
        window.clearTimeout(smoothScrollTimeoutRef.current);
      }
    },
    [],
  );

  function jumpToLatest() {
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollContainerToBottom(true);
  }

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

  function SendArrowIcon({ className }: { className?: string }) {
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
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    );
  }

  const tokenBudgetProps = {
    tokenUsage,
    tpmPerMinuteGuide: tpmInputGuideTokens,
    tpmServerSliding60s,
    modelContextMax: modelContextWindowTokens,
    inputBudgetTarget: maxInputTokensTarget,
  } as const;

  /** Same pill + field styling for welcome and active conversation (all viewports). */
  const composerShellClass =
    "w-full max-w-2xl rounded-2xl border border-slate-200/90 bg-white p-2.5 shadow-[0_12px_48px_-12px_rgba(15,23,42,0.14)] sm:rounded-[1.75rem] sm:p-2.5 md:p-2.5";
  const composerInputClass =
    "min-h-[48px] min-w-0 flex-1 rounded-xl border border-transparent bg-slate-50/90 px-3 py-2.5 text-base text-brand-900 outline-none ring-0 placeholder:text-brand-muted/55 focus:border-brand-400/40 focus:bg-white focus:ring-2 focus:ring-brand-500/15 disabled:opacity-50 sm:border-0 sm:bg-transparent sm:px-2 sm:py-2 sm:focus:ring-0";
  const composerSendIconButtonClass =
    "flex h-12 w-12 min-h-[48px] min-w-[48px] shrink-0 items-center justify-center rounded-full bg-brand-700 text-white transition-opacity hover:opacity-90 active:opacity-90 disabled:bg-brand-300 disabled:opacity-60 sm:min-h-[44px] sm:min-w-[44px]";
  /**
   * Used inside the same scroll container as messages (`sticky bottom-0`).
   * Top is fully transparent so scrolled text shows through; fades to a light veil above the dock.
   */
  const composerBottomWrapClass =
    "w-full bg-[linear-gradient(180deg,transparent_0%,transparent_15%,rgba(245,248,255,0.35)_45%,rgba(255,255,255,0.75)_100%)] px-4 pb-[max(1rem,env(safe-area-inset-bottom,0px))] pt-10 sm:px-6 sm:pb-[max(1rem,env(safe-area-inset-bottom,0px))] sm:pt-12";

  const composerDisabled = (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-center text-sm text-amber-700">
      Enter your bluebot token in Settings to start chatting.
    </div>
  );

  const composerWelcome = disabled ? (
    composerDisabled
  ) : (
    <div className={composerShellClass}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
        className="flex flex-nowrap items-center gap-2 md:gap-2"
      >
        <TokenBudgetPopover
          {...tokenBudgetProps}
          panelPlacement="below"
          welcomeIdleSpin
        />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message bluebot Assistant…"
            disabled={isProcessing}
            autoComplete="off"
            className={composerInputClass}
            enterKeyHint="send"
            inputMode="text"
          />
          <button
            type="submit"
            disabled={isProcessing || !input.trim()}
            className={composerSendIconButtonClass}
            aria-label="Send message"
          >
            <SendArrowIcon className="h-5 w-5" />
          </button>
        </div>
      </form>
    </div>
  );

  const composerFooter = disabled ? (
    composerDisabled
  ) : (
    <div className={`${composerShellClass} mx-auto`}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
        className="flex flex-nowrap items-center gap-2 md:gap-2"
      >
        <TokenBudgetPopover {...tokenBudgetProps} />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about health, flow, or pipe setup (serial number)..."
            disabled={isProcessing}
            autoComplete="off"
            className={composerInputClass}
            enterKeyHint="send"
            inputMode="text"
          />
          <button
            type="submit"
            disabled={isProcessing || !input.trim()}
            className={composerSendIconButtonClass}
            aria-label="Send message"
          >
            <SendArrowIcon className="h-5 w-5" />
          </button>
        </div>
      </form>
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header — full width of main pane, inset from sidebar via px-6 */}
      <header className="shrink-0 border-b border-brand-border/90 bg-gradient-to-b from-white to-brand-50/40 pt-[env(safe-area-inset-top,0px)] shadow-[0_1px_0_0_rgba(15,23,42,0.04)] backdrop-blur-md">
        <div
          className={`text-left sm:px-6 ${
            narrowNav
              ? "flex min-h-[2.75rem] items-center gap-3 px-4 py-3.5"
              : "px-4 py-4 sm:py-3.5"
          }`}
        >
          {narrowNav ? (
            <div className="flex shrink-0 items-center">
              <button
                type="button"
                onClick={narrowNav.onOpenSidebar}
                className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-[border-color,box-shadow,background-color] hover:border-brand-400 hover:bg-brand-50"
                title="Open conversations"
                aria-label="Open conversations sidebar"
              >
                <img
                  src="/api/logo"
                  alt=""
                  width={32}
                  height={32}
                  className="h-8 w-8 rounded-md object-cover"
                />
              </button>
            </div>
          ) : null}
          <div className="min-w-0 flex-1">
            <h1
              className={
                narrowNav
                  ? "text-xl font-bold leading-none tracking-tight text-brand-900"
                  : "text-lg font-bold tracking-tight text-brand-900 sm:text-[1.0625rem]"
              }
            >
              bluebot Assistant
            </h1>
            <p className="hidden max-w-[40rem] text-sm leading-relaxed text-brand-muted lg:mt-0.5 lg:block lg:text-xs lg:leading-snug">
              Flow analysis, meter health, and pipe configuration — ask with a serial number.
            </p>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col bg-brand-50">
        {historyLoading && messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-16 text-brand-muted sm:px-6">
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
          <div
            ref={setScrollEl}
            className="relative min-h-0 flex-1 overflow-y-auto overscroll-y-contain [-webkit-overflow-scrolling:touch]"
          >
            <div
              ref={setTranscriptInnerEl}
              className="mx-auto max-w-3xl space-y-3 px-4 py-4 pb-36 sm:px-6 sm:pb-40"
            >
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
            <div className="sticky bottom-0 z-10 w-full pointer-events-none">
              {showJumpToLatest && (
                <div className="pointer-events-none flex justify-center pb-2">
                  <button
                    type="button"
                    onClick={jumpToLatest}
                    className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-brand-border bg-white/95 px-3 py-1.5 text-xs font-medium text-brand-700 shadow-md backdrop-blur transition-opacity hover:bg-white"
                    aria-label="Jump to latest message"
                  >
                    <svg
                      className="h-3.5 w-3.5"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden
                    >
                      <path d="M12 5v14M5 12l7 7 7-7" />
                    </svg>
                    Jump to latest
                  </button>
                </div>
              )}
              <div className={composerBottomWrapClass}>
                <div className="pointer-events-auto mx-auto max-w-3xl">{composerFooter}</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden overscroll-y-contain [-webkit-overflow-scrolling:touch]">
              <div
                className={`flex min-h-0 flex-1 flex-col items-center px-[max(1rem,env(safe-area-inset-left,0px))] pr-[max(1rem,env(safe-area-inset-right,0px))] sm:px-6 ${
                  welcomeComposerAtBottom ? "pb-36 sm:pb-40" : ""
                }`}
              >
                <div
                  className="flex w-full max-w-2xl flex-1 flex-col items-center justify-center py-6 sm:py-10 md:py-14"
                >
                  <h2 className="px-1 text-center text-2xl font-semibold leading-snug tracking-tight text-brand-900 sm:px-2 sm:text-3xl">
                    What can I help with?
                  </h2>
                  <p className="mt-3 max-w-md px-1 text-center text-[0.9375rem] leading-relaxed text-brand-muted sm:mt-2 sm:px-2 sm:text-[0.9375rem]">
                    Ask about meter health, flow analysis, or pipe configuration — include a serial number when you can.
                  </p>
                  {!welcomeComposerAtBottom && (
                    <div className="mt-6 w-full max-w-full sm:mt-8 sm:px-1">
                      {composerWelcome}
                    </div>
                  )}
                </div>
                {/*
                  Mobile/tablet: skip the WelcomeCard entirely. It contains
                  its own "Meter serial for shortcuts" text input which,
                  combined with the sticky-bottom chat composer, made the
                  small-screen welcome look like a duplicated text field.
                  Desktop keeps the full suggestions UI because the inputs
                  are visually well-separated and clearly labeled there.
                */}
                {!welcomeComposerAtBottom && (
                  <div className="w-full max-w-3xl shrink-0 pb-[max(2rem,env(safe-area-inset-bottom,0px))] pt-2 sm:pb-10 sm:pt-2">
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
                  </div>
                )}
              </div>
              {welcomeComposerAtBottom && (
                <div className="sticky bottom-0 z-10 w-full pointer-events-none">
                  <div className={composerBottomWrapClass}>
                    <div className="pointer-events-auto mx-auto max-w-3xl">{composerWelcome}</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
