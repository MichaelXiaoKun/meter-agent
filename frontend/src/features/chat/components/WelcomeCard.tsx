import { useState } from "react";

function readStoredSerial(): string {
  try {
    return sessionStorage.getItem("bb_welcome_serial") ?? "";
  } catch {
    return "";
  }
}

type QuickAction = {
  id: string;
  /** Short suggested-question label shown on the pill. */
  label: string;
  message: (serial: string) => string;
};

/**
 * Suggested questions stay intentionally lightweight: they should feel like a
 * quiet prompt surface, not a second form competing with the main composer.
 */
const QUICK_ACTIONS: QuickAction[] = [
  {
    id: "health",
    label: "Is this meter healthy?",
    message: (s) => `Run a health check on meter ${s}`,
  },
  {
    id: "flow-anomaly",
    label: "Why did flow change?",
    message: (s) => `Analyze the last 24 hours of flow data for meter ${s} and explain any anomalies`,
  },
  {
    id: "compare-range",
    label: "Compare two periods",
    message: (s) => `Compare the last 24 hours of flow data for meter ${s} against the previous 24 hours`,
  },
  {
    id: "safe-config",
    label: "Update pipe setup",
    message: (s) =>
      `Configure pipe for serial ${s}: PVC, Schedule 40, 2 inch nominal, transducer angle 45º. Ask me to confirm before applying.`,
  },
  {
    id: "angle-sweep",
    label: "Test signal angles",
    message: (s) =>
      `Try all allowed transducer angles for meter ${s} and compare signal quality after each setting`,
  },
];

interface WelcomeCardProps {
  /** Full message ready to send (serial already interpolated). */
  onCompose: (message: string) => void;
  /**
   * Slightly tighter spacing for the mobile/tablet welcome layout. Suggested
   * questions still fill the main composer instead of presenting a separate
   * serial form.
   */
  compact?: boolean;
  actions?: QuickAction[];
  requireSerial?: boolean;
  hint?: string;
}

const SERIAL_PLACEHOLDER = "<METER SERIAL>";

export default function WelcomeCard({
  onCompose,
  compact = false,
  actions = QUICK_ACTIONS,
  requireSerial = true,
  hint,
}: WelcomeCardProps) {
  const [serial] = useState(readStoredSerial);

  function runAction(build: (s: string) => string) {
    const t = serial.trim();
    if (!requireSerial) {
      onCompose(build(""));
      return;
    }
    onCompose(build(t || SERIAL_PLACEHOLDER));
  }

  const savedSerialHint = requireSerial && serial.trim()
    ? `Using saved meter ${serial.trim()}`
    : null;
  const helperText = hint ?? savedSerialHint;

  const suggestedQuestions = (
    <div className="mx-auto w-full max-w-2xl px-2">
      {helperText ? (
        <p className="mb-2 text-center text-[11px] leading-snug text-brand-muted/70">
          {helperText}
        </p>
      ) : null}
      <ul
        role="list"
        aria-label="Suggested questions"
        className={[
          "flex flex-wrap justify-center gap-2 transition-opacity duration-300 ease-out",
          "opacity-65 hover:opacity-100 focus-within:opacity-100",
          compact ? "px-1" : "",
        ].join(" ")}
      >
        {actions.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => runAction(a.message)}
              className="inline-flex min-h-[2.15rem] items-center rounded-full border border-brand-border/55 bg-white/55 px-3.5 py-1.5 text-xs font-medium text-brand-muted shadow-[0_1px_2px_rgba(15,23,42,0.035)] backdrop-blur transition hover:border-brand-300 hover:bg-white hover:text-brand-900 active:scale-[0.98] active:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2 dark:border-brand-border/70 dark:bg-white/[0.04] dark:text-brand-muted/85 dark:hover:border-brand-border dark:hover:bg-white/[0.09] dark:hover:text-brand-900 dark:active:bg-white/[0.08] sm:text-[0.8125rem]"
            >
              {a.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  if (compact) {
    return (
      <div className="w-full">
        {suggestedQuestions}
      </div>
    );
  }

  return (
    <div className="w-full">
      {suggestedQuestions}
    </div>
  );
}

export type { QuickAction };
