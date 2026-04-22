import type { TurnActivityStep } from "../turnActivity";
import { useMediaQuery } from "../hooks/useMediaQuery";

interface TurnActivityTimelineProps {
  steps: TurnActivityStep[];
  /** True while the chat POST is still in flight */
  active: boolean;
}

function isResponding(
  step: TurnActivityStep,
  isLast: boolean,
  active: boolean
): boolean {
  if (!active || !isLast) return false;
  if (step.kind === "done" || step.kind === "error") return false;
  if (step.kind === "tool" && step.phase === "done") return false;
  return true;
}

const COMPACT_MAIN: Record<TurnActivityStep["kind"], string> = {
  connecting: "Sending",
  intent_route: "Scope",
  queued: "Queued",
  thinking: "Thinking",
  context: "Context",
  compressing: "Tighten",
  tool: "Tool",
  stream: "Writing",
  done: "Done",
  error: "Error",
};

function mainLineText(step: TurnActivityStep, compact: boolean): string {
  if (!compact) return step.title;
  if (step.kind === "tool" && step.title) {
    return step.title.replace(/…\s*$/u, "").trim() || COMPACT_MAIN.tool;
  }
  return COMPACT_MAIN[step.kind] ?? step.title;
}

function bodyLineText(step: TurnActivityStep, compact: boolean): string | undefined {
  if (!compact) return step.detail;
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
        return step.detail?.trim() || undefined;
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

  const rail = responding ? (
    <span
      className="mt-1.5 block h-3.5 w-0.5 shrink-0 justify-self-end rounded-full bg-neutral-400/90 dark:bg-neutral-500"
      aria-hidden
    />
  ) : isDone && !isError && step.kind !== "connecting" ? (
    <span
      className="mt-1.5 text-center text-[0.65rem] leading-none text-emerald-600/60 dark:text-emerald-500/50"
      aria-hidden
    >
      ·
    </span>
  ) : isError ? (
    <span
      className="mt-1.5 text-center text-[0.65rem] leading-none text-red-500/70"
      aria-hidden
    >
      ·
    </span>
  ) : (
    <span className="block w-0.5 shrink-0 opacity-0" aria-hidden />
  );

  return (
    <div
      className={[
        "w-full min-w-0",
        isLast ? "pb-0" : "pb-3.5",
      ].join(" ")}
    >
      <div className="grid w-full min-w-0 grid-cols-[0.5rem_1fr] items-start gap-x-2 gap-y-0">
        {rail}
        <p className={mainCls.join(" ")}>{main}</p>
        {body ? (
          <>
            <span className="min-w-0" aria-hidden />
            <p
              className={[
                "min-w-0 max-w-2xl whitespace-pre-wrap text-left leading-relaxed",
                compact ? "text-[11px] mt-0.5" : "text-xs mt-0.5",
                isError
                  ? "text-red-600/80 dark:text-red-400/85"
                  : "text-neutral-400 dark:text-neutral-500",
              ].join(" ")}
            >
              {body}
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}

export default function TurnActivityTimeline({
  steps,
  active,
}: TurnActivityTimelineProps) {
  const compact = useMediaQuery("(max-width: 640px)");
  if (steps.length === 0) return null;
  const lastIdx = steps.length - 1;

  return (
    <div
      className="flex w-full max-w-2xl justify-start text-left"
      role="status"
      aria-live="polite"
      aria-relevant="additions text"
    >
      <div className="w-full min-w-0 pl-0">
        <div className="flex flex-col">
          {steps.map((step, i) => (
            <StepRow
              key={`${step.seq}-${step.kind}-${i}`}
              step={step}
              isLast={i === lastIdx}
              active={active}
              compact={compact}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
