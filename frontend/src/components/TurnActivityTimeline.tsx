import type { TurnActivityStep } from "../turnActivity";

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

function StepRow({
  step,
  isLast,
  active,
}: {
  step: TurnActivityStep;
  isLast: boolean;
  active: boolean;
}) {
  const responding = isResponding(step, isLast, active);
  const complete = !responding;

  return (
    <div
      className={[
        "rounded-xl px-3 py-2 transition-colors duration-200",
        responding
          ? "border border-brand-300/80 bg-brand-50/90 shadow-sm ring-1 ring-brand-400/25"
          : "border border-transparent bg-transparent opacity-80",
      ].join(" ")}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
          {step.kind === "error" ? (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-100 text-[11px] font-bold text-red-700">
              !
            </span>
          ) : complete && (step.kind === "done" || (step.kind === "tool_result" && step.ok)) ? (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-[11px] text-emerald-700">
              ✓
            </span>
          ) : complete && step.kind === "tool_result" && step.ok === false ? (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-100 text-[11px] text-amber-800">
              ✗
            </span>
          ) : responding ? (
            <span className="relative flex h-5 w-5 items-center justify-center">
              <span className="absolute inset-0 animate-ping rounded-full bg-brand-400/35" />
              <span className="relative flex h-3.5 w-3.5 rounded-full bg-brand-500 shadow-sm" />
            </span>
          ) : (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-100/80 text-[10px] font-medium text-brand-600">
              ✓
            </span>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={[
              "leading-snug",
              responding ? "text-sm font-semibold text-brand-950" : "text-[13px] font-medium text-brand-800/90",
            ].join(" ")}
          >
            {step.title}
          </p>
          {step.detail ? (
            <p
              className={[
                "mt-0.5 whitespace-pre-wrap break-words leading-relaxed",
                responding ? "text-xs text-brand-700/95" : "text-[11px] text-brand-muted",
              ].join(" ")}
            >
              {step.detail}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Live “responding steps” strip: reads like assistant activity (current step pulses),
 * prior steps stay visible but de-emphasized.
 */
export default function TurnActivityTimeline({
  steps,
  active,
}: TurnActivityTimelineProps) {
  if (steps.length === 0) return null;

  const lastIdx = steps.length - 1;

  return (
    <div className="flex justify-start" role="status" aria-live="polite" aria-relevant="additions text">
      <div className="w-full max-w-[75%] rounded-2xl border border-brand-border/80 bg-white/95 px-2 py-2 shadow-sm backdrop-blur-sm">
        <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-brand-muted/90">
          {active ? "Responding" : "Steps"}
        </p>
        <div className="flex flex-col gap-1">
          {steps.map((step, i) => (
            <StepRow
              key={`${step.seq}-${step.kind}-${i}`}
              step={step}
              isLast={i === lastIdx}
              active={active}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
