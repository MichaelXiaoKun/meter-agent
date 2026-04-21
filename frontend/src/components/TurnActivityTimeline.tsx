import type { TurnActivityStep } from "../turnActivity";
import { useMediaQuery } from "../hooks/useMediaQuery";

interface TurnActivityTimelineProps {
  steps: TurnActivityStep[];
  /** True while the chat POST is still in flight */
  active: boolean;
}

/** True for the single in-flight step at the bottom while the request is open. */
function isResponding(
  step: TurnActivityStep,
  isLast: boolean,
  active: boolean
): boolean {
  return active && isLast && step.kind !== "done" && step.kind !== "error";
}

// ---------------------------------------------------------------------------
// Phone-friendly labels
// ---------------------------------------------------------------------------
// The full desktop labels (``Preparing request``, ``Conversation context``)
// are fine on a 14" display but read as noise on a 375-px phone where the
// timeline competes with the reply bubble for the same vertical budget. In
// compact mode we keep the stage title short ("Thinking", "Writing") and
// swap the detail line for a **one-liner** that summarizes what the stage
// actually did. Unlike desktop, on mobile the polling transport batches
// SSE events (one fetch returns all events since the last poll) so React
// may render several state transitions in a single frame — the user sees
// the *final* state of the card without watching each step pulse live.
// Showing a concrete detail per step is therefore the only way the
// "multi-stage state machine" view is actually legible on phones.
const COMPACT_TITLE: Record<TurnActivityStep["kind"], string> = {
  queued: "Queued",
  thinking: "Thinking",
  context: "Context",
  compressing: "Compressing",
  tool_call: "Tool call",
  tool_progress: "Tool call",
  tool_result: "Tool call",
  stream: "Writing reply",
  done: "Done",
  error: "Error",
};

function compactTitle(step: TurnActivityStep): string {
  // Tool stages already carry a useful tool name — prefer that.
  if (
    (step.kind === "tool_call" ||
      step.kind === "tool_progress" ||
      step.kind === "tool_result") &&
    step.title
  ) {
    // ``tool_result`` title is "<Tool> — finished/failed" on desktop;
    // the icon already communicates success/failure in compact mode.
    return step.title.replace(/\s[—-].*$/u, "");
  }
  return COMPACT_TITLE[step.kind] ?? step.title;
}

/**
 * Produce a short one-liner for compact rendering.
 *
 * On desktop each row can show the verbose detail from
 * ``turnActivity.ts`` ("Waiting on Claude…", "Streaming assistant
 * response"). On phones those become repetitive once you have 4-5 rows
 * stacked, so we collapse them to a tight fragment — just enough to
 * make "what did this stage do" legible at a glance.
 */
function compactDetail(step: TurnActivityStep): string | undefined {
  switch (step.kind) {
    case "queued":
      return "Waiting for a free slot";
    case "thinking":
      return "Waiting on Claude";
    case "context":
      // Desktop: "~12% of model window · 1,234 input tokens (estimate)"
      // Phone: keep only the percentage fragment.
      return step.detail?.split("·")[0]?.trim();
    case "compressing":
      return "Summarizing older messages";
    case "tool_call":
      return "Calling tool";
    case "tool_progress":
      return step.detail;
    case "tool_result":
      return step.ok === false ? "Tool call failed" : "Tool finished";
    case "stream":
      return "Streaming response";
    case "done":
      return "Reply saved";
    case "error":
      return step.detail;
    default:
      return step.detail;
  }
}

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
  const complete = !responding;
  const title = compact ? compactTitle(step) : step.title;
  // Compact mode shows a concise detail for *every* row (not just the
  // active one). Rationale: on phones the polling transport may deliver
  // several events in one batch, so React renders the final timeline
  // state without the user watching each pulse transition. A per-row
  // one-liner is the only way the multi-stage coordination is still
  // readable.
  const detail = compact ? compactDetail(step) : step.detail;

  return (
    <div
      className={[
        "transition-colors duration-200",
        compact ? "rounded-md px-2 py-1" : "rounded-xl px-3 py-2",
        responding
          ? compact
            ? "bg-brand-50/90 ring-1 ring-brand-300/60"
            : "border border-brand-300/80 bg-brand-50/90 shadow-sm ring-1 ring-brand-400/25"
          : compact
            ? "bg-transparent"
            : "border border-transparent bg-transparent opacity-80",
      ].join(" ")}
    >
      <div className={["flex items-start", compact ? "gap-2" : "gap-2.5"].join(" ")}>
        <span
          className={[
            "shrink-0 flex items-center justify-center",
            compact ? "mt-0.5 h-4 w-4" : "mt-0.5 h-5 w-5",
          ].join(" ")}
        >
          {step.kind === "error" ? (
            <span
              className={[
                "flex items-center justify-center rounded-full bg-red-100 font-bold text-red-700",
                compact ? "h-4 w-4 text-[10px]" : "h-5 w-5 text-[11px]",
              ].join(" ")}
            >
              !
            </span>
          ) : complete && (step.kind === "done" || (step.kind === "tool_result" && step.ok)) ? (
            <span
              className={[
                "flex items-center justify-center rounded-full bg-emerald-100 text-emerald-700",
                compact ? "h-4 w-4 text-[10px]" : "h-5 w-5 text-[11px]",
              ].join(" ")}
            >
              ✓
            </span>
          ) : complete && step.kind === "tool_result" && step.ok === false ? (
            <span
              className={[
                "flex items-center justify-center rounded-full bg-amber-100 text-amber-800",
                compact ? "h-4 w-4 text-[10px]" : "h-5 w-5 text-[11px]",
              ].join(" ")}
            >
              ✗
            </span>
          ) : responding ? (
            <span
              className={[
                "relative flex items-center justify-center",
                compact ? "h-4 w-4" : "h-5 w-5",
              ].join(" ")}
            >
              <span className="absolute inset-0 animate-ping rounded-full bg-brand-400/35" />
              <span
                className={[
                  "relative rounded-full bg-brand-500 shadow-sm",
                  compact ? "h-2.5 w-2.5" : "h-3.5 w-3.5",
                ].join(" ")}
              />
            </span>
          ) : (
            <span
              className={[
                "flex items-center justify-center rounded-full bg-brand-100/80 font-medium text-brand-600",
                compact ? "h-4 w-4 text-[9px]" : "h-5 w-5 text-[10px]",
              ].join(" ")}
            >
              ✓
            </span>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={[
              "leading-snug",
              responding
                ? compact
                  ? "text-[13px] font-semibold text-brand-950"
                  : "text-sm font-semibold text-brand-950"
                : compact
                  ? "truncate text-[12px] font-medium text-brand-800/90"
                  : "text-[13px] font-medium text-brand-800/90",
            ].join(" ")}
          >
            {title}
          </p>
          {detail ? (
            <p
              className={[
                "mt-0.5 whitespace-pre-wrap break-words leading-relaxed",
                responding
                  ? compact
                    ? "text-[11px] text-brand-700/95"
                    : "text-xs text-brand-700/95"
                  : compact
                    ? "text-[11px] text-brand-muted"
                    : "text-[11px] text-brand-muted",
              ].join(" ")}
            >
              {detail}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Live "responding steps" strip: reads like assistant activity (current step
 * pulses), prior steps stay visible but de-emphasized.
 *
 * On phones (``max-width: 640px``) the component switches to a compact
 * 1-line-per-stage layout so the state-machine view still fits above the
 * reply bubble without pushing it out of the viewport. Stage titles are
 * shortened, details are collapsed to short one-liners, and the card
 * stays narrow — but *every* stage still carries its own summary so the
 * multi-stage coordination remains legible even when the polling
 * transport delivers several events in a single React batch.
 */
export default function TurnActivityTimeline({
  steps,
  active,
}: TurnActivityTimelineProps) {
  const compact = useMediaQuery("(max-width: 640px)");

  if (steps.length === 0) return null;

  const lastIdx = steps.length - 1;

  return (
    <div
      className="flex justify-start"
      role="status"
      aria-live="polite"
      aria-relevant="additions text"
    >
      <div
        className={[
          "min-w-0 rounded-2xl border border-brand-border/80 bg-white/95 shadow-sm backdrop-blur-sm",
          compact ? "w-full max-w-[94%] px-1.5 py-1.5" : "w-full max-w-[75%] px-2 py-2",
        ].join(" ")}
      >
        <p
          className={[
            "font-semibold uppercase tracking-wider text-brand-muted/90",
            compact
              ? "px-1.5 pb-1 text-[9.5px]"
              : "px-2 pb-1.5 text-[10px]",
          ].join(" ")}
        >
          {active ? "Responding" : "Steps"}
        </p>
        <div className={["flex flex-col", compact ? "gap-0.5" : "gap-1"].join(" ")}>
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
