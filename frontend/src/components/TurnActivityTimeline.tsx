import { motion, AnimatePresence } from "framer-motion";
import type { TurnActivityStep } from "../turnActivity";
import { useMediaQuery } from "../hooks/useMediaQuery";

interface TurnActivityTimelineProps {
  steps: TurnActivityStep[];
  /** True while the chat POST is still in flight */
  active: boolean;
  /**
   * When false, the outer wrapper is a plain div (no ``aria-live``) — use for
   * continuation rows rendered below streamed markdown so the live region
   * stays a single logical strip.
   */
  announce?: boolean;
}

function isResponding(
  step: TurnActivityStep,
  isLast: boolean,
  active: boolean
): boolean {
  if (!active || !isLast) return false;
  if (step.kind === "done" || step.kind === "error") return false;
  if (step.kind === "tool" && step.phase === "done") return false;
  if (step.kind === "thinking" && /^Thought for\b/u.test(step.title)) return false;
  return true;
}

const COMPACT_MAIN: Record<TurnActivityStep["kind"], string> = {
  connecting: "Sending",
  intent_route: "Scope",
  queued: "Queued",
  thinking: "Reasoning",
  context: "Usage",
  compressing: "Tighten",
  tool: "Tool",
  stream: "Generating",
  done: "Done",
  error: "Error",
};

const STEP_ICONS: Record<TurnActivityStep["kind"], string> = {
  connecting: "↗",
  intent_route: "🎯",
  queued: "⏳",
  thinking: "💭",
  context: "📊",
  compressing: "🗜",
  tool: "⚙️",
  stream: "✨",
  done: "✓",
  error: "✕",
};

const STEP_COLORS: Record<TurnActivityStep["kind"], { bg: string; text: string }> = {
  connecting: { bg: "bg-blue-50 dark:bg-blue-950/30", text: "text-blue-700 dark:text-blue-300" },
  intent_route: { bg: "bg-purple-50 dark:bg-purple-950/30", text: "text-purple-700 dark:text-purple-300" },
  queued: { bg: "bg-amber-50 dark:bg-amber-950/30", text: "text-amber-700 dark:text-amber-300" },
  thinking: { bg: "bg-indigo-50 dark:bg-indigo-950/30", text: "text-indigo-700 dark:text-indigo-300" },
  context: { bg: "bg-cyan-50 dark:bg-cyan-950/30", text: "text-cyan-700 dark:text-cyan-300" },
  compressing: { bg: "bg-violet-50 dark:bg-violet-950/30", text: "text-violet-700 dark:text-violet-300" },
  tool: { bg: "bg-green-50 dark:bg-green-950/30", text: "text-green-700 dark:text-green-300" },
  stream: { bg: "bg-pink-50 dark:bg-pink-950/30", text: "text-pink-700 dark:text-pink-300" },
  done: { bg: "bg-emerald-50 dark:bg-emerald-950/30", text: "text-emerald-700 dark:text-emerald-300" },
  error: { bg: "bg-red-50 dark:bg-red-950/30", text: "text-red-700 dark:text-red-300" },
};

function mainLineText(step: TurnActivityStep, compact: boolean): string {
  if (!compact) return step.title;
  if (step.kind === "thinking") return step.title;
  if (step.kind === "tool" && step.title) {
    return step.title.replace(/…\s*$/u, "").trim() || COMPACT_MAIN.tool;
  }
  return COMPACT_MAIN[step.kind] ?? step.title;
}

function bodyLineText(step: TurnActivityStep, compact: boolean): string | undefined {
  if (!compact) {
    if (step.kind === "tool" && step.progressLines && step.progressLines.length > 0) {
      // Staged list replaces ``detail`` — except a failed tool still shows the error in ``detail``.
      if (!(step.phase === "done" && !step.ok)) {
        return undefined;
      }
    }
    return step.detail;
  }
  switch (step.kind) {
    case "connecting":
      return;
    case "intent_route":
      return;
    case "queued":
      return;
    case "thinking":
      return;
    case "context":
      return step.detail?.split("·")[0]?.trim();
    case "compressing":
      return "Shorter thread";
    case "tool":
      if (step.phase === "running") {
        if (step.progressLines && step.progressLines.length > 0) {
          if (compact) {
            return (
              step.progressLines[step.progressLines.length - 1]?.trim() || undefined
            );
          }
          return undefined;
        }
        return step.detail?.trim() || undefined;
      }
      if (step.phase === "done" && step.ok && step.progressLines?.length) {
        if (compact) {
          return `${step.progressLines.length} update(s)`;
        }
        return undefined;
      }
      return;
    case "stream":
      return;
    case "done":
      return;
    case "error":
      return step.detail;
    default:
      return step.detail;
  }
}

/**
 * Interleaved, low-contrast status lines (mainstream agent-style: gray copy,
 * optional body under a step, clear line breaks between events).
 */
function StepRow({
  step,
  isLast,
  active,
  compact,
}: {
  step: TurnActivityStep;
  isLast: boolean;
  active: boolean;
  compact: boolean;
}) {
  const responding = isResponding(step, isLast, active);
  const main = mainLineText(step, compact);
  const body = bodyLineText(step, compact);

  const isError = step.kind === "error" || (step.kind === "tool" && step.phase === "done" && step.ok === false);
  const isDone = step.kind === "done" || (step.kind === "tool" && step.phase === "done" && step.ok);

  const colors = STEP_COLORS[step.kind];
  const icon = STEP_ICONS[step.kind];

  const mainCls = [
    "leading-relaxed",
    compact ? "text-xs" : "text-[13px]",
    responding
      ? "font-medium text-neutral-600 dark:text-neutral-200 agent-activity-line-active"
      : isError
        ? "text-red-600/90 dark:text-red-400/90"
        : isDone
          ? "text-neutral-500 dark:text-neutral-400"
          : "text-neutral-500/95 dark:text-neutral-500",
  ];

  const toolProgressList =
    step.kind === "tool" &&
    step.progressLines &&
    step.progressLines.length > 0 &&
    !compact;

  const rail = responding ? (
    <span
      className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-medium ${colors.bg} ${colors.text} animate-pulse`}
      aria-hidden
    >
      {icon}
    </span>
  ) : (
    <span
      className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-medium ${colors.bg} ${colors.text}`}
      aria-hidden
    >
      {icon}
    </span>
  );

  return (
    <div
      className={[
        "w-full min-w-0",
        isLast ? "pb-0" : "pb-3.5",
      ].join(" ")}
    >
      {/*
        Keep title → staged lines → body in a single right-hand column. Flattening
        more cells into the 2-col grid can let auto-placement order the <ul> above
        the <p> (progress looked “above” the first status line).
      */}
      <div className="grid w-full min-w-0 grid-cols-[1.5rem_1fr] items-start gap-x-2">
        <div className="flex justify-center pt-0.5">{rail}</div>
        <div className="min-w-0">
          <p className={mainCls.join(" ")}>{main}</p>
          {toolProgressList ? (
            <ul
              className="mt-1.5 max-w-2xl space-y-1.5 border-l-2 border-brand-500/25 py-0.5 pl-2.5 dark:border-brand-500/20"
            >
              {step.progressLines!.map((line, j) => (
                <li
                  key={j}
                  className="list-none text-left text-xs leading-relaxed text-neutral-500 dark:text-neutral-400"
                >
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
          {body ? (
            <p
              className={[
                "min-w-0 max-w-2xl whitespace-pre-wrap text-left leading-relaxed",
                compact ? "text-[11px] mt-0.5" : "text-xs mt-0.5",
                toolProgressList && !isError ? "mt-1.5" : "",
                isError
                  ? "text-red-600/80 dark:text-red-400/85"
                  : "text-neutral-400 dark:text-neutral-500",
              ].join(" ")}
            >
              {body}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function TurnActivityTimeline({
  steps,
  active,
  announce = true,
}: TurnActivityTimelineProps) {
  const compact = useMediaQuery("(max-width: 640px)");
  // Hide intent_route ("Scoping: …") — implementation detail, not user-facing work.
  const safeSteps = steps.filter(
    (s): s is TurnActivityStep =>
      s != null &&
      typeof s.kind === "string" &&
      s.kind !== "intent_route"
  );
  if (safeSteps.length === 0) return null;
  const lastIdx = safeSteps.length - 1;

  const liveProps = announce
    ? ({
        role: "status" as const,
        "aria-live": "polite" as const,
        "aria-relevant": "additions text" as const,
      } as const)
    : {};

  return (
    <div
      className="flex w-full max-w-2xl justify-start text-left"
      {...liveProps}
    >
      <div className="w-full min-w-0 pl-0">
        <div className="flex flex-col">
          <AnimatePresence mode="popLayout">
            {safeSteps.map((step, i) => (
              <motion.div
                key={`${step.seq}-${step.kind}-${i}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -8 }}
                transition={{ duration: 0.2, delay: i * 0.04 }}
              >
                <StepRow
                  step={step}
                  isLast={i === lastIdx}
                  active={active}
                  compact={compact}
                />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
