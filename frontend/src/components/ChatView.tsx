import {
  useEffect,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { motion, AnimatePresence, LayoutGroup } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { DownloadArtifact, Message, PlotAttachment, SSEEvent, Ticket, TicketStatus } from "../types";
import type { AgentStatus } from "../hooks/useChat";
import AnimatedMessageBubble from "./AnimatedMessageBubble";
import { extractPlotAttachments } from "./plotAttachments";
import { extractDownloadArtifacts } from "../artifactAttachments";
import { PlotGrouped } from "./PlotImage";
import ArtifactLinks from "./ArtifactLinks";
import WelcomeCard from "./WelcomeCard";
import type { QuickAction } from "./WelcomeCard";
import WelcomeBluebotLogo from "./WelcomeBluebotLogo";
import TurnActivityTimeline from "./TurnActivityTimeline";
import { MessageSkeleton } from "./MessageSkeleton";
import { TokenBudgetPopover } from "./TokenBudget";
import ModelPicker from "./ModelPicker";
import MicButton from "./MicButton";
import ThemeToggle from "./ThemeToggle";
import SharePopover from "./SharePopover";
import ConfigConfirmationCard from "./ConfigConfirmationCard";
import SweepResultCard from "./SweepResultCard";
import { IconSidebarDock } from "./SidebarIconRail";
import type { OrchestratorModelOption, PublicShareToken } from "../api";
import { createTicket, listTickets, updateTicket } from "../api";
import {
  splitActivityAtFirstTool,
  splitTurnActivityAroundStreamBody,
  type TurnActivityStep,
} from "../turnActivity";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import { buildMeterWorkspace } from "../meterWorkspace";
import MeterWorkspacePanel from "./MeterWorkspacePanel";
import {
  configAngle,
  configSerial,
  configSweepAngles,
  configSweepRangeLabel,
} from "../configCompat";

type ConfigWorkflow = NonNullable<SSEEvent["config_workflow"]>;
type ToastFn = (a: {
  kind: "success" | "error";
  title: string;
  message?: string;
}) => void;
type ChatSendOptions = {
  confirmedActionId?: string | null;
  cancelledActionId?: string | null;
  supersededActionId?: string | null;
};

const CHAT_LOGO_LAYOUT_ID = "bluebot-chat-header-logo";
const CHAT_COMPOSER_LAYOUT_ID = "bluebot-chat-composer";
const CHAT_LOGO_LAYOUT_TRANSITION = {
  type: "spring" as const,
  stiffness: 420,
  damping: 34,
  mass: 0.8,
};
const CHAT_COMPOSER_LAYOUT_TRANSITION = {
  type: "spring" as const,
  stiffness: 360,
  damping: 36,
  mass: 0.95,
};

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

function StreamingAssistantBubble({ markdown }: { markdown: string }) {
  return (
    <motion.div
      className="flex justify-start"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="max-w-2xl min-w-0 w-full py-1">
        <div className="prose prose-sm max-w-none min-w-0 break-words prose-p:my-1 prose-a:break-words prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm dark:prose-invert dark:prose-headings:text-brand-900 [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_table]:block [&_table]:overflow-x-auto [&_img]:max-w-full">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
        </div>
      </div>
    </motion.div>
  );
}

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
  /** Assistant markdown before the first tool in this turn (may be empty). */
  streamingLead: string;
  /** Assistant markdown after tools (main streamed reply). */
  streamingTail: string;
  pendingPlots: PlotAttachment[];
  pendingArtifacts: DownloadArtifact[];
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
  workspaceEvents: SSEEvent[];
  serverProcessing: boolean;
  onSend: (text: string, options?: ChatSendOptions) => void;
  onConfirmConfig: (workflow: ConfigWorkflow) => void;
  onCancelConfig: (workflow: ConfigWorkflow) => void;
  onCancel?: () => void;
  onDismissAssistantError?: () => void;
  disabled: boolean;
  /**
   * Mobile (narrow) layout: no left rail — bluebot logo in the header opens the drawer.
   */
  narrowNav?: {
    onOpenSidebar: () => void;
  };
  /**
   * Claude model allowlist from ``/api/config`` — populates the composer's
   * :component:`ModelPicker`. Undefined / empty hides the picker.
   */
  availableModels?: OrchestratorModelOption[];
  /** Currently selected model ID (``null`` = use server default). */
  selectedModel?: string | null;
  /** Called with the new model ID when the user picks one. */
  onSelectModel?: (modelId: string) => void;
  accessToken?: string | null;
  userId?: string | null;
  anthropicApiKey?: string | null;
  onToast?: ToastFn;
  /**
   * When set, shows Share in the header (export PDF + public snapshot link).
   * Gated by the parent: only pass when a conversation is selected and has messages.
   */
  share?: {
    userId?: string;
    accessToken?: string;
    conversationTitle: string;
    onToast: (a: { kind: "success" | "error"; title: string; message?: string }) => void;
    createShareLink?: (conversationId: string) => Promise<PublicShareToken | string>;
    revokeShareLink?: (token: string, revokeKey?: string) => Promise<void>;
  };
  copy?: {
    title?: string;
    titleClassName?: string;
    subtitle?: string;
    welcomeTitle?: string;
    welcomePlaceholder?: string;
    composerPlaceholder?: string;
    welcomeActions?: QuickAction[];
    welcomeHint?: string;
    requireWelcomeSerial?: boolean;
  };
  /** Hide the right-side meter workspace for surfaces that do not use live meters. */
  showWorkspacePanel?: boolean;
}

export default function ChatView({
  conversationId,
  messages,
  status,
  streamingLead,
  streamingTail,
  pendingPlots,
  pendingArtifacts,
  tokenUsage,
  historyLoading,
  tpmInputGuideTokens,
  tpmServerSliding60s,
  modelContextWindowTokens,
  maxInputTokensTarget,
  turnActivity,
  turnActivityActive,
  workspaceEvents,
  serverProcessing,
  onSend,
  onConfirmConfig,
  onCancelConfig,
  onCancel,
  onDismissAssistantError,
  disabled,
  narrowNav,
  availableModels,
  selectedModel,
  onSelectModel,
  accessToken,
  userId,
  anthropicApiKey,
  onToast,
  share,
  copy,
  showWorkspacePanel = true,
}: ChatViewProps) {
  const [input, setInput] = useState("");
  const [composerFocused, setComposerFocused] = useState(false);
  const [logoAcknowledgeSignal, setLogoAcknowledgeSignal] = useState(0);
  const [replacingConfigActionId, setReplacingConfigActionId] = useState<string | null>(null);
  /**
   * Voice-to-text dictation state. See :hook:`useSpeechRecognition`.
   *
   * The mic button snapshots the current textarea value into
   * ``speechBaselineRef`` at the moment the user taps "record", and while
   * the session is active we mirror
   *
   *   textarea = baseline + finalText [+ " " + interim]
   *
   * into :state:`input`. That way the user sees their dictation land in
   * the composer live, but typed text entered before the session started
   * is preserved (we don't overwrite "Hi, here's the serial: " with the
   * spoken addendum — we prepend to it).
   *
   * If the user manually edits the textarea while listening, we treat
   * their edit as the new baseline so interim results can't clobber it.
   */
  const speech = useSpeechRecognition();
  const speechBaselineRef = useRef<string>("");
  const lastAppliedFinalRef = useRef<string>("");
  /**
   * Shared by the desktop ``<input>`` and the mobile auto-grow
   * ``<textarea>`` so suggestion taps from :component:`WelcomeCard` can
   * focus / select placeholder text in both variants.
   */
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
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
   * The composer is ``position: sticky; bottom: 0`` inside this same
   * scroll container so content can flow behind the transparent input
   * bar. Setting ``scrollTop = scrollHeight`` lands at the true end of
   * scrollable content; the browser clamps to ``scrollHeight -
   * clientHeight``, which positions the last message directly above the
   * composer (the sticky dock then occupies its natural slot at the very
   * bottom of the flex column). On short replies the ``flex-1`` spacer
   * above the transcript absorbs all the slack so ``scrollHeight`` equals
   * ``clientHeight`` and this call is a harmless clamp-to-0 no-op.
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
   * as the user scrolls up by more than the active "near-bottom" threshold
   * we flip to ``false`` so they can read history without being yanked back.
   * We mirror the value into a ref so the autoscroll effect can read the
   * latest value without re-running the listener-attach effect.
   */
  /**
   * Distance-from-bottom (in CSS px) that we still consider "at the bottom"
   * **for the Jump-to-latest pill**. This is intentionally generous so a
   * finger flick or momentum overshoot on a phone doesn't flash the pill on
   * every interaction.
   */
  const NEAR_BOTTOM_PX_MOUSE = 80;
  const NEAR_BOTTOM_PX_TOUCH = 200;
  /**
   * Distance-from-bottom (in CSS px) below which we keep
   * ``stickToBottomRef`` ``true`` — i.e. we will keep auto-snapping to the
   * latest content as it streams in / images decode / the keyboard opens.
   *
   * This MUST be much smaller than the pill threshold above. The previous
   * implementation reused the 200 px touch threshold here, which meant a
   * deliberate ~30-50 px scroll-up by the user wasn't enough to "leave
   * the bottom" — so the very next ResizeObserver tick (streaming) or
   * ``visualViewport`` tick (URL bar collapsing during their scroll on iOS
   * Safari) would auto-snap them right back to the bottom, undoing their
   * scroll. Anything past ~32 px is unmistakably an intentional gesture
   * even on a coarse pointer, so we cut sticky-bottom there regardless of
   * input modality.
   */
  const STICK_BOTTOM_PX = 32;
  /**
   * Minimum overflow above the sticky composer (in CSS px) before we flip
   * the scroll container from ``overflow-y-hidden`` → ``overflow-y-auto``.
   * Kept tiny — even a one-line overflow on a phone is worth letting the
   * user reach by scrolling.
   */
  const ENABLE_SCROLL_OVERFLOW_PX = 8;
  const isCoarsePointer = useMediaQuery("(pointer: coarse)");
  const nearBottomThresholdRef = useRef(NEAR_BOTTOM_PX_MOUSE);
  useEffect(() => {
    nearBottomThresholdRef.current = isCoarsePointer
      ? NEAR_BOTTOM_PX_TOUCH
      : NEAR_BOTTOM_PX_MOUSE;
  }, [isCoarsePointer]);
  const stickToBottomRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  /**
   * Mirrors ``showJumpToLatest`` availability: ``true`` only when the
   * transcript *above* the sticky composer is actually taller than the
   * viewport. We flip the scroll container between ``overflow-y-auto``
   * and ``overflow-y-hidden`` on this flag so short replies can't be
   * wheel-scrolled past the sticky dock — there is nothing above to
   * reveal, and a tiny bit of scroll travel there feels like a bug.
   */
  const [transcriptOverflowsViewport, setTranscriptOverflowsViewport] = useState(false);
  /**
   * Stashed reference to the current ``hasMeaningfulTranscriptAbove`` closure
   * from ``setScrollEl`` so the transcript ``ResizeObserver`` can re-check
   * whether the content now overflows the viewport (e.g. after streaming
   * text grew the bubble, or a plot image decoded). Using a ref avoids
   * tearing down/recreating the observer on every rerender.
   */
  const scrollRecomputeRef = useRef<(() => boolean) | null>(null);
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
    /**
     * Compute the two booleans the rest of the scroll logic cares about,
     * carefully separating "is there enough overflow to enable scrolling
     * at all?" from "is there enough overflow to bother showing the
     * Jump-to-latest pill?".
     *
     *   • ``overflows`` (sets ``transcriptOverflowsViewport``) — even a
     *     few px of real scroll distance should let the user scroll. The
     *     sticky composer overlays the bottom ``dockHeight`` px of the
     *     viewport, so when a response is just barely longer than
     *     ``clientHeight - dockHeight`` the *only* way for the user to
     *     see the last lines is to scroll up by ``dockHeight`` and lift
     *     the floating input off them. We must NOT subtract dockHeight
     *     here — that scroll distance is the whole point.
     *   • Return value (drives the pill) — pill / "near-bottom" decisions
     *     use the touch-aware threshold so a finger flick doesn't flicker
     *     the pill and so auto-scroll keeps sticky-bottom for thumb
     *     wobbles.
     *
     * Notes on viewport sizing:
     *   - We deliberately use ``el.clientHeight`` (the layout viewport of
     *     the scroll container) and NOT ``window.visualViewport.height``.
     *     On iOS Safari, opening the keyboard shrinks visualViewport but
     *     leaves the layout viewport (and our scroll container) intact;
     *     mixing them caused phantom overflow into empty space. The
     *     visualViewport listener still triggers recomputes when the
     *     layout itself reflows (URL bar collapse with ``100dvh``-style
     *     layouts, orientation change, Android keyboard with
     *     ``interactive-widget=resizes-content``).
     */
    const hasMeaningfulTranscriptAbove = () => {
      const maxScrollTop = el.scrollHeight - el.clientHeight;
      const overflows = maxScrollTop > ENABLE_SCROLL_OVERFLOW_PX;
      setTranscriptOverflowsViewport((prev) => (prev === overflows ? prev : overflows));
      return maxScrollTop > nearBottomThresholdRef.current;
    };
    scrollRecomputeRef.current = hasMeaningfulTranscriptAbove;
    const recomputeFromScrollPos = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      // Sticky-bottom uses a tight threshold so that any deliberate scroll
      // gesture (more than ~32 px) immediately releases sticky mode. If we
      // reused the larger pill threshold, an intentional 50 px scroll-up
      // would still register as "still at the bottom" and the next content-
      // size or viewport tick would yank them back.
      stickToBottomRef.current = distanceFromBottom < STICK_BOTTOM_PX;
      // Pill visibility uses the larger touch-aware threshold so a casual
      // finger flick doesn't flash it on screen.
      const nearForPill = distanceFromBottom < nearBottomThresholdRef.current;
      const meaningful = hasMeaningfulTranscriptAbove();
      const shouldShow = !nearForPill && meaningful;
      setShowJumpToLatest((prev) => (prev === shouldShow ? prev : shouldShow));
    };
    const onScroll = () => {
      // Don't let an in-flight programmatic smooth scroll flip the sticky
      // state mid-animation — the user explicitly asked to land at the
      // bottom, and the intermediate distances would otherwise hide the
      // pill and back again, then mark them as "scrolled away".
      if (smoothScrollInFlightRef.current) return;
      recomputeFromScrollPos();
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
      recomputeFromScrollPos();
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
      // Re-evaluate "does the transcript actually overflow?" on every size
      // change so the scroll container flips between ``auto`` and
      // ``hidden`` the moment a streamed reply grows past the viewport (or
      // shrinks back, e.g. on a new-conversation reset).
      scrollRecomputeRef.current?.();
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
  }, [
    messages,
    streamingLead,
    streamingTail,
    status,
    turnActivity,
    turnActivityActive,
    pendingPlots,
    workspaceEvents,
  ]);

  /**
   * Window-switch behaviour:
   * - idle/history view: start at top (first message first), ChatGPT-style
   * - active generation: stay pinned to bottom so fresh output remains visible
   */
  useLayoutEffect(() => {
    const el = scrollElRef.current;
    if (!el) return;

    const shouldLandAtBottom = isProcessing || serverProcessing;
    stickToBottomRef.current = shouldLandAtBottom;
    setTranscriptOverflowsViewport(false);

    if (shouldLandAtBottom) {
      setShowJumpToLatest(false);
      scrollContainerToBottom();
    } else {
      el.scrollTo({ top: 0, behavior: "auto" });
    }

    const raf = requestAnimationFrame(() => {
      if (shouldLandAtBottom) {
        scrollContainerToBottom();
        scrollRecomputeRef.current?.();
        return;
      }
      const meaningful = scrollRecomputeRef.current?.() ?? false;
      setShowJumpToLatest(meaningful);
    });
    return () => cancelAnimationFrame(raf);
    // Deliberately keyed to conversation/window switches; generation state changes
    // are handled below so finishing a turn does not snap the transcript to top.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, historyLoading]);

  // A running turn should always bring the user back to the latest activity,
  // but finishing that same turn must not snap the transcript back to the top.
  useEffect(() => {
    if (!isProcessing && !serverProcessing) return;
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollContainerToBottom();
    const raf = requestAnimationFrame(() => {
      scrollContainerToBottom();
      scrollRecomputeRef.current?.();
    });
    return () => cancelAnimationFrame(raf);
  }, [isProcessing, serverProcessing]);

  // Confirmation cards are high-priority user interactions. When one appears,
  // keep it visible instead of preserving an older scrolled-up position.
  useEffect(() => {
    const pendingConfirmation = (() => {
      for (let i = workspaceEvents.length - 1; i >= 0; i -= 1) {
        const workflow = workspaceEvents[i]?.config_workflow;
        if (workflow?.action_id) return workflow.status === "pending_confirmation";
      }
      return false;
    })();
    if (!pendingConfirmation) return;
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollContainerToBottom();
    const raf = requestAnimationFrame(() => {
      scrollContainerToBottom();
      scrollRecomputeRef.current?.();
    });
    return () => cancelAnimationFrame(raf);
  }, [workspaceEvents]);

  useEffect(
    () => () => {
      if (smoothScrollTimeoutRef.current != null) {
        window.clearTimeout(smoothScrollTimeoutRef.current);
      }
    },
    [],
  );

  /**
   * Re-evaluate "is there meaningful transcript above?" / "should the pill
   * show?" / "should scroll be enabled?" whenever the *visible* viewport
   * changes — even if the scroll container's own ``clientHeight`` doesn't.
   *
   * IMPORTANT: this hook only **recomputes pill/overflow state** — it
   * deliberately does NOT call ``scrollContainerToBottom`` on viewport
   * changes. On iOS Safari, the user's own scroll gesture causes the URL
   * bar to collapse, which fires ``visualViewport.scroll``/``resize``;
   * if we re-snap to bottom inside this handler we end up undoing the
   * user's scroll a fraction of a second after they finish it ("scroll up
   * a little, it jumps back to the original position"). The streaming
   * ``useEffect`` below already re-snaps when *content* grows, and the
   * conversation-switch ``useLayoutEffect`` re-snaps on conversation
   * change — those are the only two cases where an automatic snap is
   * actually wanted.
   *
   * ``ResizeObserver`` on the transcript inner div doesn't fire for
   * URL-bar collapse / keyboard show because the transcript node itself
   * doesn't change size — only the visible window into it does. So we
   * still need this hook for the recompute side-effect (pill visibility,
   * ``transcriptOverflowsViewport``), just not for re-snapping.
   */
  useEffect(() => {
    const recompute = () => scrollRecomputeRef.current?.();
    const vv = typeof window !== "undefined" ? window.visualViewport : null;
    vv?.addEventListener("resize", recompute);
    vv?.addEventListener("scroll", recompute);
    window.addEventListener("resize", recompute);
    window.addEventListener("orientationchange", recompute);
    return () => {
      vv?.removeEventListener("resize", recompute);
      vv?.removeEventListener("scroll", recompute);
      window.removeEventListener("resize", recompute);
      window.removeEventListener("orientationchange", recompute);
    };
  }, []);

  function jumpToLatest() {
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    scrollContainerToBottom(true);
  }

  function forceBottomNow() {
    stickToBottomRef.current = true;
    setShowJumpToLatest(false);
    requestAnimationFrame(() => scrollContainerToBottom());
  }

  function confirmConfig(workflow: ConfigWorkflow) {
    setReplacingConfigActionId(null);
    forceBottomNow();
    onConfirmConfig(workflow);
  }

  function cancelConfig(workflow: ConfigWorkflow) {
    setReplacingConfigActionId(null);
    forceBottomNow();
    onCancelConfig(workflow);
  }

  function handleSubmit(text?: string) {
    const msg = text ?? input;
    if (!msg.trim() || disabled) return;
    // Guarantee any in-flight dictation is aborted before we clear the input;
    // otherwise the recogniser's trailing ``onresult`` could re-populate the
    // textarea after the user has already submitted.
    if (speech.listening) speech.stop();
    const supersededActionId = replacingConfigActionId;
    setReplacingConfigActionId(null);
    setLogoAcknowledgeSignal((n) => n + 1);
    forceBottomNow();
    onSend(msg, supersededActionId ? { supersededActionId } : undefined);
    setInput("");
    lastAppliedFinalRef.current = "";
    inputRef.current?.focus();
  }

  /**
   * Voice-input toggle. Tapping the mic while idle starts a dictation
   * session; tapping again stops it. We snapshot the current textarea
   * contents into ``speechBaselineRef`` so interim/final transcript is
   * appended to — rather than replacing — text the user typed manually.
   */
  function toggleVoiceInput() {
    if (disabled || isProcessing || !speech.voiceApiAvailable || !speech.usable) return;
    if (speech.listening) {
      speech.stop();
      return;
    }
    const trimmedTail = input.length > 0 && !/\s$/.test(input) ? input + " " : input;
    speechBaselineRef.current = trimmedTail;
    lastAppliedFinalRef.current = "";
    speech.start();
  }

  /**
   * While the recogniser is active, mirror ``baseline + finalText +
   * interim`` into the textarea so dictation lands live. On the first
   * render after ``listening`` flips back to false, we keep whatever
   * ``finalText`` has accumulated (it's already been applied via the
   * baseline) and drop the interim tail — the final result is the one
   * the user will edit / send.
   */
  useEffect(() => {
    if (!speech.listening && !speech.interim && !speech.finalText) return;
    // If a new final chunk arrived, bake it into the baseline so it's
    // preserved if the user starts typing and interrupts the live mirror.
    const newFinalSuffix = speech.finalText.slice(lastAppliedFinalRef.current.length);
    if (newFinalSuffix) {
      speechBaselineRef.current += newFinalSuffix;
      lastAppliedFinalRef.current = speech.finalText;
    }
    const interimSuffix = speech.interim
      ? (speechBaselineRef.current && !/\s$/.test(speechBaselineRef.current)
        ? " "
        : "") + speech.interim
      : "";
    setInput(speechBaselineRef.current + interimSuffix);
  }, [speech.listening, speech.interim, speech.finalText]);

  /**
   * Auto-grow the composer textarea so long messages **wrap** onto
   * additional lines (up to a 6-line cap, then internal scroll) instead of
   * being hidden behind a single-line horizontal scroll.
   *
   * The composer lays out as **two rows** on every viewport: the textarea
   * spans the full width of the pill on row 1; the merged settings button
   * + send button sit on row 2 (left / right respectively). That layout is
   * static — we only drive the textarea's height here, never re-arrange
   * children based on measurement, which keeps the composer from flashing
   * mid-keystroke.
   *
   * ``useLayoutEffect`` so the height is applied before paint and the user
   * never sees a frame of clipped content.
   */
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el || el.tagName !== "TEXTAREA") return;
    el.style.height = "auto";
    const style = getComputedStyle(el);
    const lineHeight = parseFloat(style.lineHeight) || 24;
    const paddingY =
      (parseFloat(style.paddingTop) || 0) +
      (parseFloat(style.paddingBottom) || 0);
    const max = 6 * lineHeight + paddingY; /* ~6 lines, then internal scroll */
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, [input]);

  // Keep the transcript + status visible while a turn is running even if messages were cleared
  // briefly (load effect, Strict Mode) — otherwise the whole pane becomes Welcome + idle.
  const statusActive =
    status.kind !== "idle" && status.kind !== "error";
  const hasMessages =
    messages.length > 0 ||
    !!streamingLead.trim() ||
    !!streamingTail.trim() ||
    pendingArtifacts.length > 0 ||
    pendingPlots.length > 0 ||
    statusActive ||
    status.kind === "error" ||
    (turnActivityActive && turnActivity.length > 0);

  /** Any streamed chars (including leading whitespace) — keeps ``done`` split in sync from the first chunk. */
  const hasStreamForSplit =
    streamingLead.length > 0 || streamingTail.length > 0;
  const { above: activityAboveStreamBody, below: activityBelowStreamBody } =
    useMemo(
      () => splitTurnActivityAroundStreamBody(turnActivity, hasStreamForSplit),
      [turnActivity, hasStreamForSplit]
    );

  const { beforeTools: activityBeforeFirstTool, fromFirstTool: activityFromFirstTool } =
    useMemo(
      () => splitActivityAtFirstTool(activityAboveStreamBody),
      [activityAboveStreamBody]
    );
  const hasToolSegment = activityFromFirstTool.length > 0;

  const showShare =
    share &&
    conversationId != null &&
    messages.length > 0;
  const showHeaderLogo = hasMessages;
  const welcomeLogoMood = speech.listening
    ? "listening"
    : composerFocused || input.trim().length > 0
      ? "drafting"
      : "idle";
  const welcomeLogoExpression = disabled || speech.blockReason || speech.error
    ? "annoyed"
    : input.includes("<METER SERIAL>")
      ? "confused"
      : "neutral";
  const headerLogoMood =
    isProcessing || serverProcessing || historyLoading ? "loading" : "idle";
  const headerLogoExpression = status.kind === "error" ? "annoyed" : "neutral";
  const headerTitle = copy?.title ?? "FlowIQ";
  const headerTitleClassName = copy?.titleClassName ?? "text-brand-700 dark:text-brand-700";
  const headerSubtitle =
    copy?.subtitle ??
    "by bluebot · Data-backed flow insights and expert recommendations.";
  const welcomeTitle = copy?.welcomeTitle ?? "What can I help with?";
  const welcomePlaceholder = copy?.welcomePlaceholder ?? "Message FlowIQ...";
  const composerPlaceholder =
    copy?.composerPlaceholder ?? "Ask FlowIQ about meter health, flow trends, or pipe setup...";

  // Pair plot paths with assistant messages: collect from tool_result rows,
  // attach to the next assistant message (same logic as the Streamlit app).
  const plotsByIndex = new Map<number, PlotAttachment[]>();
  const artifactsByIndex = new Map<number, DownloadArtifact[]>();
  {
    let queued: PlotAttachment[] = [];
    let queuedArtifacts: DownloadArtifact[] = [];
    messages.forEach((msg, i) => {
      const next = extractPlotAttachments(msg.content);
      if (next.length > 0) {
        queued.push(...next);
      }
      const nextArtifacts = extractDownloadArtifacts(msg.content);
      if (nextArtifacts.length > 0) {
        queuedArtifacts.push(...nextArtifacts);
      }
      if (msg.role === "assistant" && queued.length > 0) {
        plotsByIndex.set(i, [...queued]);
        queued = [];
      }
      if (msg.role === "assistant" && queuedArtifacts.length > 0) {
        artifactsByIndex.set(i, [...queuedArtifacts]);
        queuedArtifacts = [];
      }
    });
  }

  const tokenBudgetProps = {
    tokenUsage,
    tpmPerMinuteGuide: tpmInputGuideTokens,
    tpmServerSliding60s,
    modelContextMax: modelContextWindowTokens,
    inputBudgetTarget: maxInputTokensTarget,
  } as const;
  const workspaceEnabled = showWorkspacePanel;

  const workspace = useMemo(
    () => (workspaceEnabled ? buildMeterWorkspace(messages, workspaceEvents) : buildMeterWorkspace([], [])),
    [messages, workspaceEnabled, workspaceEvents],
  );
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const ticketToolEventCount = useMemo(
    () =>
      workspaceEvents.filter(
        (event) =>
          event.type === "tool_result" &&
          (event.tool === "create_ticket" ||
            event.tool === "update_ticket" ||
            event.tool === "list_tickets"),
      ).length,
    [workspaceEvents],
  );
  const refreshTickets = useCallback(
    async (signal?: AbortSignal) => {
      if (!workspaceEnabled || !userId || !conversationId) {
        setTickets([]);
        return;
      }
      const rows = await listTickets(userId, {
        conversationId,
        serialNumber: workspace.serialNumber ?? null,
        status: ["open", "in_progress", "waiting_on_human"],
        signal,
      });
      setTickets(rows);
    },
    [conversationId, userId, workspace.serialNumber, workspaceEnabled],
  );

  useEffect(() => {
    const ac = new AbortController();
    refreshTickets(ac.signal).catch((err) => {
      if ((err as Error).name !== "AbortError") {
        console.error("Failed to load tickets:", err);
      }
    });
    return () => ac.abort();
  }, [refreshTickets, messages.length, status.kind, ticketToolEventCount]);

  async function handleTicketStatus(ticket: Ticket, nextStatus: TicketStatus) {
    if (!userId || !accessToken) return;
    try {
      const note =
        nextStatus === "resolved"
          ? "Resolved from the admin workspace."
          : nextStatus === "cancelled"
            ? "Cancelled from the admin workspace."
            : `Moved to ${nextStatus.replaceAll("_", " ")} from the admin workspace.`;
      await updateTicket(
        ticket.id,
        {
          user_id: userId,
          status: nextStatus,
          note,
          evidence:
            nextStatus === "resolved"
              ? { source: "admin_workspace", ticket_id: ticket.id }
              : undefined,
        },
        accessToken,
      );
      await refreshTickets();
    } catch (err) {
      onToast?.({
        kind: "error",
        title: "Ticket update failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleTicketClaim(ticket: Ticket) {
    if (!userId || !accessToken) return;
    try {
      await updateTicket(
        ticket.id,
        {
          user_id: userId,
          owner_type: "human",
          owner_id: userId,
          note: "Claimed from the admin workspace.",
        },
        accessToken,
      );
      await refreshTickets();
    } catch (err) {
      onToast?.({
        kind: "error",
        title: "Ticket update failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleTrackNextAction(label: string) {
    if (!userId || !accessToken || !conversationId) return;
    const serial = workspace.serialNumber ?? null;
    try {
      await createTicket(
        {
          user_id: userId,
          conversation_id: conversationId,
          serial_number: serial,
          title: label,
          description: serial
            ? `Follow up on ${label} for meter ${serial}.`
            : `Follow up on ${label}.`,
          success_criteria: serial
            ? `Complete "${label}" for meter ${serial} and record the outcome.`
            : `Complete "${label}" and record the outcome.`,
          priority: "normal",
          owner_type: "human",
          owner_id: userId,
          metadata: { source: "meter_workspace_next_action" },
        },
        accessToken,
      );
      await refreshTickets();
      onToast?.({ kind: "success", title: "Ticket created", message: label });
    } catch (err) {
      onToast?.({
        kind: "error",
        title: "Ticket creation failed",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }

  const liveConfigWorkflow = useMemo(() => {
    for (let i = workspaceEvents.length - 1; i >= 0; i -= 1) {
      const workflow = workspaceEvents[i]?.config_workflow;
      if (workflow?.action_id) return workflow;
    }
    return null;
  }, [workspaceEvents]);
  const liveSweepResult = useMemo(() => {
    for (let i = workspaceEvents.length - 1; i >= 0; i -= 1) {
      const result = workspaceEvents[i]?.sweep_result;
      if (result) return result;
    }
    return null;
  }, [workspaceEvents]);

  function composeAlternativeConfig(workflow: NonNullable<SSEEvent["config_workflow"]>) {
    const serial = configSerial(workflow);
    const angle = configAngle(workflow.proposed_values);
    setReplacingConfigActionId(workflow.action_id ?? null);
    const text =
      configSweepAngles(workflow.proposed_values).length > 0 ||
      configSweepRangeLabel(workflow.proposed_values)
      ? `Instead, sweep meter ${serial || "<METER SERIAL>"} transducer angles `
      : angle
        ? `Instead, set meter ${serial || "<METER SERIAL>"} transducer angle to `
        : `Instead, configure meter ${serial || "<METER SERIAL>"} with `;
    setInput(text);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const pos = text.length;
      el.setSelectionRange(pos, pos);
    });
  }

  /** Same pill + field styling for welcome and active conversation (all viewports). */
  const composerShellClass =
    "w-full max-w-2xl rounded-2xl border border-brand-border/90 bg-white p-2.5 shadow-[0_12px_48px_-12px_rgba(15,23,42,0.14)] focus-within:ring-2 focus-within:ring-brand-500/30 transition-shadow dark:border-brand-border dark:bg-brand-100 dark:shadow-[0_12px_48px_-12px_rgba(0,0,0,0.45)] sm:rounded-[1.75rem] sm:p-2.5 md:p-2.5";
  /**
   * Auto-grow ``<textarea>`` used by the unified composer. The textarea
   * occupies its own row at the top of the pill and visually blends into
   * it (transparent background, no inner border), so the only visible
   * boundary is the pill itself — same layout on desktop, tablet and
   * phone. ``resize-none`` disables the native corner handle; height is
   * driven imperatively from the :func:`useLayoutEffect` above (up to
   * ~6 lines, then the textarea scrolls internally).
   */
  const composerTextareaClass =
    "block min-h-[36px] w-full min-w-0 resize-none rounded-none border-0 bg-transparent px-1.5 py-1 text-base leading-6 text-brand-900 outline-none ring-0 placeholder:text-brand-muted/55 focus:outline-none focus:ring-0 disabled:opacity-50 transition-[height] duration-150 ease-out dark:placeholder:text-brand-muted/50";
  const composerSendIconButtonClass =
    "flex h-12 w-12 min-h-[48px] min-w-[48px] shrink-0 items-center justify-center rounded-full bg-brand-700 text-white transition-opacity hover:opacity-90 active:opacity-90 active:scale-95 transition-transform disabled:bg-brand-300 disabled:opacity-60 disabled:cursor-not-allowed sm:min-h-[44px] sm:min-w-[44px]";
  /**
   * Composer wrapper — fully transparent so the input field blends into the
   * surrounding chat area (no opaque "bottom header" bar). On the hasMessages
   * layout the composer sits below the scroll region, so "transparent" here
   * means it inherits the chat-area background. On the welcome screen
   * (``welcomeComposerAtBottom``) it's still ``sticky bottom-0`` inside the
   * scroll container, and transparency lets whatever scrolled past the
   * composer remain visible underneath.
   */
  const composerBottomWrapClass =
    "w-full bg-transparent px-4 pb-[max(1rem,env(safe-area-inset-bottom,0px))] pt-4 sm:px-6 sm:pb-[max(1rem,env(safe-area-inset-bottom,0px))] sm:pt-4";

  const composerDisabled = (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-center text-sm text-amber-700 dark:border-amber-900/45 dark:bg-amber-950/35 dark:text-amber-200">
      Enter your bluebot token in Settings to start chatting.
    </div>
  );

  /**
   * Send/Cancel button shared by welcome / footer composers.
   * Shows send arrow when idle, stop icon when processing.
   */
  const sendButton = (
    <button
      type={isProcessing && onCancel ? "button" : "submit"}
      onClick={isProcessing && onCancel ? onCancel : undefined}
      disabled={!isProcessing && !input.trim()}
      className={composerSendIconButtonClass}
      aria-label={isProcessing && onCancel ? "Stop processing" : "Send message"}
      title={isProcessing && onCancel ? "Stop" : undefined}
    >
      <AnimatePresence mode="wait">
        {isProcessing && onCancel ? (
          <motion.svg
            key="stop"
            className="h-5 w-5"
            viewBox="0 0 24 24"
            fill="currentColor"
            aria-hidden
            initial={{ opacity: 0, scale: 0.7 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.7 }}
            transition={{ duration: 0.2 }}
          >
            <rect x="6" y="6" width="12" height="12" />
          </motion.svg>
        ) : (
          <motion.div
            key="send"
            initial={{ opacity: 0, scale: 0.7 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.7 }}
            transition={{ duration: 0.2 }}
          >
            <SendArrowIcon className="h-5 w-5" />
          </motion.div>
        )}
      </AnimatePresence>
    </button>
  );

  /**
   * Handle keyboard shortcuts on the composer textarea.
   *
   * - Fine pointer (desktop): bare ``Enter`` submits; ``Shift+Enter`` and
   *   ``(Meta|Ctrl)+Enter`` insert a newline (the latter preserves the
   *   familiar macOS/Windows habit).
   * - Coarse pointer (mobile/tablet): ``Enter`` always inserts a newline;
   *   the on-screen send button is the only way to submit. That matches
   *   native messaging apps and avoids accidental submits while typing a
   *   multi-line serial / description.
   */
  function handleComposerKeyDown(
    e: React.KeyboardEvent<HTMLTextAreaElement>,
  ) {
    if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
    if (isCoarsePointer) return;
    if (e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    handleSubmit();
  }

  /**
   * Unified composer body used on every viewport (desktop, tablet, phone):
   *
   * 1. Row 1 — auto-grow ``<textarea>`` spans the full width of the pill.
   *    Long messages wrap onto additional lines (up to a 6-line cap, then
   *    internal scroll) and stay fully visible.
   * 2. Row 2 — :component:`TokenBudgetPopover` + :component:`ModelPicker`
   *    as two separate controls on the left, send button on the right.
   *    Both popovers open **above** because the composer sits at the
   *    bottom of the chat area (or at the bottom of the viewport while
   *    the welcome screen is on a narrow device).
   *
   * Modeled after modern chat composers (ChatGPT / Claude) where the
   * controls live beneath the input, not beside it. Keeps the pill
   * footprint stable as the textarea grows and avoids any "button jumps
   * to a new row" layout shift.
   */
  function composerBody(placeholder: string, welcomeIdle: boolean) {
    return (
      <div className="flex flex-col gap-1.5">
        <textarea
          ref={inputRef as RefObject<HTMLTextAreaElement>}
          value={input}
          onChange={(e) => {
            const next = e.target.value;
            // If the user types while dictation is active, re-baseline to
            // their edited text so the next interim result appends to what
            // they actually see, not to the pre-edit snapshot.
            if (speech.listening) {
              speechBaselineRef.current = next;
              lastAppliedFinalRef.current = speech.finalText;
            }
            setInput(next);
          }}
          onKeyDown={handleComposerKeyDown}
          onFocus={() => setComposerFocused(true)}
          onBlur={() => setComposerFocused(false)}
          placeholder={speech.listening ? "Listening…" : placeholder}
          disabled={isProcessing}
          autoComplete="off"
          rows={1}
          className={composerTextareaClass}
          enterKeyHint={isCoarsePointer ? "enter" : "send"}
          inputMode="text"
        />
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <TokenBudgetPopover
              {...tokenBudgetProps}
              panelPlacement="above"
              welcomeIdleSpin={welcomeIdle}
            />
            {onSelectModel && (
              <ModelPicker
                models={availableModels}
                value={selectedModel ?? null}
                onChange={onSelectModel}
                disabled={isProcessing}
                panelPlacement="above"
              />
            )}
          </div>
          <div className="flex items-center gap-2">
            {speech.voiceApiAvailable && (
              <MicButton
                listening={speech.listening}
                disabled={disabled || isProcessing || !speech.usable}
                error={speech.blockReason ?? speech.error}
                onToggle={toggleVoiceInput}
              />
            )}
            {sendButton}
          </div>
        </div>
      </div>
    );
  }

  const composerWelcome = disabled ? (
    composerDisabled
  ) : (
    <motion.div
      layoutId={CHAT_COMPOSER_LAYOUT_ID}
      className={composerShellClass}
      transition={CHAT_COMPOSER_LAYOUT_TRANSITION}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
      >
        {composerBody(welcomePlaceholder, true)}
      </form>
    </motion.div>
  );

  const composerFooter = disabled ? (
    composerDisabled
  ) : (
    <motion.div
      layoutId={CHAT_COMPOSER_LAYOUT_ID}
      className={`${composerShellClass} mx-auto`}
      transition={CHAT_COMPOSER_LAYOUT_TRANSITION}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSubmit();
        }}
      >
        {composerBody(
          composerPlaceholder,
          false,
        )}
      </form>
    </motion.div>
  );

  return (
    <LayoutGroup>
      <div className="flex h-full min-h-0 min-w-0 flex-col lg:flex-row">
      <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* Header — full width of main pane, inset from sidebar via px-6 */}
      <header className="relative z-20 shrink-0 border-b border-brand-border/90 bg-gradient-to-b from-white to-brand-50/40 pt-[env(safe-area-inset-top,0px)] shadow-[0_1px_0_0_rgba(15,23,42,0.04)] backdrop-blur-md dark:from-brand-100 dark:to-brand-50 dark:shadow-[0_1px_0_0_rgba(0,0,0,0.2)]">
        <div
          className={`text-left sm:px-6 ${narrowNav
            ? "flex min-h-[2.75rem] items-center gap-3 px-4 py-3.5"
            : "px-4 py-4 sm:py-3.5"
            }`}
        >
          {narrowNav ? (
            <>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={narrowNav.onOpenSidebar}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-brand-border/80 bg-white text-brand-700 shadow-sm ring-1 ring-brand-border/40 transition-[border-color,box-shadow,background-color] hover:border-brand-400 hover:bg-brand-50 dark:bg-brand-100 dark:text-brand-muted dark:hover:bg-white/10"
                  title="Open conversations"
                  aria-label="Open conversations sidebar"
                >
                  <IconSidebarDock className="h-5 w-5 shrink-0" />
                </button>
                <AnimatePresence initial={false}>
                  {showHeaderLogo && (
                    <motion.div
                      layoutId={CHAT_LOGO_LAYOUT_ID}
                      className="shrink-0"
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.96 }}
                      transition={CHAT_LOGO_LAYOUT_TRANSITION}
                    >
                      <WelcomeBluebotLogo
                        size={32}
                        mood={headerLogoMood}
                        expression={headerLogoExpression}
                        acknowledgeSignal={logoAcknowledgeSignal}
                        sleepAfterMs={null}
                        className="welcome-bluebot-header-logo"
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              <div className="min-w-0 flex-1">
                <h1 className={`text-xl font-bold leading-none tracking-tight ${headerTitleClassName}`}>
                  {headerTitle}
                </h1>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {showShare && (
                  <SharePopover
                    conversationId={conversationId!}
                    userId={share.userId}
                    accessToken={share.accessToken}
                    conversationTitle={share.conversationTitle}
                    messages={messages}
                    onToast={share.onToast}
                    createShareLink={share.createShareLink}
                    revokeShareLink={share.revokeShareLink}
                  />
                )}
                <ThemeToggle size="md" className="shrink-0" />
              </div>
            </>
          ) : (
            <div className="flex w-full min-w-0 items-start justify-between gap-3 sm:items-center">
              <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <AnimatePresence initial={false}>
                  {showHeaderLogo && (
                    <motion.div
                      layoutId={CHAT_LOGO_LAYOUT_ID}
                      className="shrink-0"
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.96 }}
                      transition={CHAT_LOGO_LAYOUT_TRANSITION}
                    >
                      <WelcomeBluebotLogo
                        size={34}
                        mood={headerLogoMood}
                        expression={headerLogoExpression}
                        acknowledgeSignal={logoAcknowledgeSignal}
                        sleepAfterMs={null}
                        className="welcome-bluebot-header-logo"
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
                <div className="min-w-0 flex-1">
                  <h1 className={`text-lg font-bold tracking-tight sm:text-[1.0625rem] ${headerTitleClassName}`}>
                    {headerTitle}
                  </h1>
                  <p className="hidden max-w-[40rem] text-sm leading-relaxed text-brand-muted lg:mt-0.5 lg:block lg:text-xs lg:leading-snug">
                    {headerSubtitle}
                  </p>
                </div>
              </div>
              {showShare && (
                <SharePopover
                  conversationId={conversationId!}
                  userId={share.userId}
                  accessToken={share.accessToken}
                  conversationTitle={share.conversationTitle}
                  messages={messages}
                  onToast={share.onToast}
                  createShareLink={share.createShareLink}
                  revokeShareLink={share.revokeShareLink}
                />
              )}
            </div>
          )}
        </div>
      </header>

      <div className="relative z-0 flex min-h-0 flex-1 flex-col bg-brand-50">
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
            /*
              Composer lives **inside** this scroll region as a
              ``position: sticky; bottom: 0`` sibling so scrolled transcript
              can flow behind the transparent input bar (including while the
              "Jump to latest" pill is visible). The layout recipe:

                • ``flex flex-col`` scroll container
                • ``flex-1`` spacer before the transcript (bottom-anchors
                  content on short replies; collapses on long ones)
                • transcript (``flex-shrink-0``) — its natural height
                • sticky composer dock (``flex-shrink-0``) at the end

              This gives:
                • short replies — spacer absorbs all slack; transcript sits
                  directly above the transparent composer with no dead zone.
                • long replies — spacer collapses to 0; transcript overflows;
                  composer stays stuck to the viewport bottom with content
                  visibly scrolling behind its transparent background.
            */
            className={`relative flex min-h-0 flex-1 flex-col overscroll-y-contain [-webkit-overflow-scrolling:touch] ${transcriptOverflowsViewport ? "overflow-y-auto" : "overflow-y-hidden"
              }`}
          >
            <div
              ref={setTranscriptInnerEl}
              className="mx-auto w-full max-w-3xl flex-shrink-0 space-y-3 px-4 py-4 sm:px-6"
            >
              <AnimatePresence>
                {status.kind === "error" && (
                  <motion.div
                    role="alert"
                    className="rounded-lg border border-red-300 bg-red-50 shadow-sm dark:border-red-800/50 dark:bg-red-950/40"
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.25 }}
                  >
                    <div className="px-4 py-3 space-y-2">
                      <div className="flex items-start gap-3">
                        <span className="text-xl shrink-0 mt-0.5">⚠️</span>
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-red-950 dark:text-red-100 text-sm">
                            Oops! Something went wrong
                          </p>
                          <p className="mt-1 text-xs text-red-800/85 dark:text-red-200/80 whitespace-pre-wrap break-words leading-relaxed">
                            {status.error}
                          </p>
                        </div>
                      </div>
                      {onDismissAssistantError && (
                        <div className="flex gap-2 justify-end pt-1">
                          <button
                            type="button"
                            onClick={onDismissAssistantError}
                            className="text-xs font-medium px-3 py-1.5 rounded-md bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-200 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors"
                          >
                            Dismiss
                          </button>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              {historyLoading && messages.length === 0 ? (
                <div className="space-y-3">
                  <MessageSkeleton />
                  <MessageSkeleton />
                  <MessageSkeleton />
                </div>
              ) : (
                <AnimatePresence mode="popLayout">
                  {messages.map((msg, i) => (
                    <AnimatedMessageBubble
                      key={i}
                      message={msg}
                      plots={plotsByIndex.get(i)}
                      artifacts={artifactsByIndex.get(i)}
                      transcript={messages}
                      messageIndex={i}
                      onConfirmConfig={confirmConfig}
                      onCancelConfig={cancelConfig}
                      onTypeOtherConfig={composeAlternativeConfig}
                      configActionsDisabled={isProcessing}
                      liveConfigEvents={workspaceEvents}
                      accessToken={accessToken}
                      anthropicApiKey={anthropicApiKey}
                      onToast={onToast}
                    />
                  ))}
                </AnimatePresence>
              )}

              {/*
                Pre-tool strip → first reply (lead) → tools / sub-agent strip →
                post-tool reply (tail) → completion. History uses one bubble only.
              */}
              {turnActivityActive && activityBeforeFirstTool.length > 0 ? (
                <TurnActivityTimeline
                  steps={activityBeforeFirstTool}
                  active={turnActivityActive && !hasToolSegment}
                />
              ) : null}
              {streamingLead.trim() ? (
                <StreamingAssistantBubble markdown={streamingLead} />
              ) : null}
              {turnActivityActive && activityFromFirstTool.length > 0 ? (
                <TurnActivityTimeline
                  steps={activityFromFirstTool}
                  active={turnActivityActive && hasToolSegment}
                />
              ) : null}
              {streamingTail.trim() ? (
                <StreamingAssistantBubble markdown={streamingTail} />
              ) : null}
              {turnActivityActive && activityBelowStreamBody.length > 0 ? (
                <TurnActivityTimeline
                  steps={activityBelowStreamBody}
                  active={false}
                  announce={false}
                />
              ) : null}

              {pendingPlots.length > 0 && (
                <div className="flex justify-start">
                  <div className="max-w-[75%]">
                    <PlotGrouped
                      plots={pendingPlots}
                      className="w-full rounded-lg border border-brand-border shadow-sm"
                    />
                  </div>
                </div>
              )}

              {pendingArtifacts.length > 0 && (
                <div className="flex justify-start">
                  <ArtifactLinks
                    artifacts={pendingArtifacts}
                    accessToken={accessToken}
                    anthropicApiKey={anthropicApiKey}
                    onToast={onToast}
                    className="max-w-2xl"
                  />
                </div>
              )}

              {liveConfigWorkflow?.status === "pending_confirmation" && (
                <ConfigConfirmationCard
                  workflow={liveConfigWorkflow}
                  disabled={isProcessing}
                  onConfirm={confirmConfig}
                  onCancel={cancelConfig}
                  onTypeOther={composeAlternativeConfig}
                />
              )}

              {liveSweepResult && (
                <SweepResultCard result={liveSweepResult} />
              )}

              {serverProcessing && status.kind === "idle" && (
                <div className="flex items-center gap-2 rounded-lg bg-brand-100 px-3 py-2 text-sm text-brand-muted dark:bg-brand-100/70">
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
            <div className="min-h-0 flex-1" aria-hidden />
            {/*
              Sticky composer dock — lives INSIDE the scroll container so
              scrolled transcript can flow behind its transparent
              background (including while the "Jump to latest" pill is
              visible). ``flex-shrink-0`` keeps it from being squeezed by
              the flex layout; ``sticky bottom-0`` pins it at the viewport
              bottom while content scrolls; ``pointer-events-none`` on the
              wrapper lets clicks pass through the transparent margins and
              is re-enabled on the actual input via ``pointer-events-auto``
              on the inner wrapper.
            */}
            <div className="sticky bottom-0 z-10 w-full flex-shrink-0 pointer-events-none">
              <AnimatePresence>
                {showJumpToLatest && (
                  <motion.div
                    className="pointer-events-none flex justify-center pb-2"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 8 }}
                    transition={{ duration: 0.2 }}
                  >
                    <button
                      type="button"
                      onClick={jumpToLatest}
                      className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-brand-border bg-white/95 px-3 py-1.5 text-xs font-medium text-brand-700 shadow-md backdrop-blur transition-opacity hover:bg-white dark:border-brand-border dark:bg-brand-50/95 dark:text-brand-muted dark:hover:bg-white/10 dark:hover:text-brand-900"
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
                  </motion.div>
                )}
              </AnimatePresence>
              <div className={composerBottomWrapClass}>
                <div className="pointer-events-auto mx-auto max-w-3xl">{composerFooter}</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden overscroll-y-contain [-webkit-overflow-scrolling:touch]">
              <div
                className={`flex min-h-0 flex-1 flex-col items-center px-[max(1rem,env(safe-area-inset-left,0px))] pr-[max(1rem,env(safe-area-inset-right,0px))] sm:px-6 ${welcomeComposerAtBottom ? "pb-36 sm:pb-40" : ""
                  }`}
              >
                <div
                  className={
                    welcomeComposerAtBottom
                      ? "flex w-full max-w-2xl flex-col items-center py-6 sm:py-8 md:py-10"
                      : "flex w-full max-w-2xl flex-1 flex-col items-center justify-center py-6 sm:py-10 md:py-14"
                  }
                >
                  <motion.div
                    layoutId={CHAT_LOGO_LAYOUT_ID}
                    className="mb-3 shrink-0 sm:mb-4"
                    transition={CHAT_LOGO_LAYOUT_TRANSITION}
                  >
                    <WelcomeBluebotLogo
                      size={welcomeComposerAtBottom ? 88 : 104}
                      mood={welcomeLogoMood}
                      expression={welcomeLogoExpression}
                      acknowledgeSignal={logoAcknowledgeSignal}
                    />
                  </motion.div>
                  <h2 className="welcome-heading px-1 text-center text-2xl font-semibold leading-snug tracking-tight text-brand-900 sm:px-2 sm:text-3xl">
                    {welcomeTitle}
                  </h2>
                  {!welcomeComposerAtBottom && (
                    <div className="mt-6 w-full max-w-full sm:mt-8 sm:px-1">
                      {composerWelcome}
                    </div>
                  )}
                </div>
                {/*
                  Welcome suggestions stay secondary to the main composer.
                  Taps prefill the composer with either a saved meter serial or
                  a ``<METER SERIAL>`` placeholder for the user to replace.
                */}
                <div
                  className={
                    welcomeComposerAtBottom
                      ? "w-full max-w-3xl shrink-0 pb-6 pt-2 sm:pb-8"
                      : "w-full max-w-3xl shrink-0 pb-[max(2rem,env(safe-area-inset-bottom,0px))] pt-2 sm:pb-10 sm:pt-2"
                  }
                >
                  <WelcomeCard
                    compact={welcomeComposerAtBottom}
                    actions={copy?.welcomeActions}
                    hint={copy?.welcomeHint}
                    requireSerial={copy?.requireWelcomeSerial ?? true}
                    onCompose={(text) => {
                      setInput(text);
                      requestAnimationFrame(() => {
                        const el = inputRef.current;
                        if (!el) return;
                        el.focus();
                        // If the suggestion came in with the placeholder
                        // (mobile / no stored serial), put the cursor on it
                        // so the user can type the serial without deleting
                        // anything else first.
                        const placeholderIdx = text.indexOf("<METER SERIAL>");
                        if (placeholderIdx >= 0) {
                          el.setSelectionRange(
                            placeholderIdx,
                            placeholderIdx + "<METER SERIAL>".length,
                          );
                        } else {
                          el.select();
                        }
                      });
                    }}
                  />
                </div>
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
      {hasMessages && workspaceEnabled && (
        <MeterWorkspacePanel
          workspace={workspace}
          processing={isProcessing || serverProcessing}
          tickets={tickets}
          onCompose={(text) => {
            setInput(text);
            requestAnimationFrame(() => inputRef.current?.focus());
          }}
          onConfirmConfig={(actionId) => {
            const workflow = workspace.pendingConfig;
            if (workflow?.action_id === actionId) {
              confirmConfig(workflow);
            }
          }}
          onTrackNextAction={handleTrackNextAction}
          onTicketClaim={handleTicketClaim}
          onTicketStatus={handleTicketStatus}
        />
      )}
      </div>
    </LayoutGroup>
  );
}
