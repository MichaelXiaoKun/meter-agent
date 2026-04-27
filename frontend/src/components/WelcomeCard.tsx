import { useEffect, useId, useRef, useState } from "react";

function readStoredSerial(): string {
  try {
    return sessionStorage.getItem("bb_welcome_serial") ?? "";
  } catch {
    return "";
  }
}

type QuickAction = {
  id: string;
  /** Short label shown on the pill (1–2 words ideally). */
  label: string;
  message: (serial: string) => string;
};

/**
 * Four core questions, kept intentionally short so the welcome screen reads as
 * a single chip row rather than a wall of cards. Subtitles are dropped — the
 * pill label is enough to disambiguate, and the user lands inside the chat
 * composer (with the message already typed) where they can edit before sending.
 */
const QUICK_ACTIONS: QuickAction[] = [
  {
    id: "health",
    label: "Health check",
    message: (s) => `Run a health check on meter ${s}`,
  },
  {
    id: "flow-anomaly",
    label: "Flow anomaly",
    message: (s) => `Analyze the last 24 hours of flow data for meter ${s} and explain any anomalies`,
  },
  {
    id: "compare-range",
    label: "Compare range",
    message: (s) => `Compare the last 24 hours of flow data for meter ${s} against the previous 24 hours`,
  },
  {
    id: "safe-config",
    label: "Configure safely",
    message: (s) =>
      `Configure pipe for serial ${s}: PVC, Schedule 40, 2 inch nominal, transducer angle 45º. Ask me to confirm before applying.`,
  },
  {
    id: "angle-sweep",
    label: "Angle sweep",
    message: (s) =>
      `Try all allowed transducer angles for meter ${s} and compare signal quality after each setting`,
  },
];

interface WelcomeCardProps {
  /** Full message ready to send (serial already interpolated). */
  onCompose: (message: string) => void;
  /**
   * Mobile/tablet variant: hide the dedicated "Meter serial for shortcuts"
   * input so the screen has a single text field (the chat composer at the
   * bottom). Suggestion buttons still work — they prefill the composer with
   * either the previously-stored serial or a ``<METER SERIAL>`` placeholder
   * so the user can finish typing it inline.
   */
  compact?: boolean;
}

const SERIAL_PLACEHOLDER = "<METER SERIAL>";

export default function WelcomeCard({ onCompose, compact = false }: WelcomeCardProps) {
  const serialId = useId();
  const serialInputRef = useRef<HTMLInputElement>(null);
  const [serial, setSerial] = useState(readStoredSerial);
  const [serialError, setSerialError] = useState(false);

  useEffect(() => {
    try {
      const t = serial.trim();
      if (t) sessionStorage.setItem("bb_welcome_serial", t);
    } catch {
      /* ignore */
    }
  }, [serial]);

  function runAction(build: (s: string) => string) {
    const t = serial.trim();
    if (!t) {
      // Compact mode (mobile): no inline serial input, so we can't focus or
      // error on it. Fall through with a placeholder the user can fill in
      // directly inside the chat composer.
      if (compact) {
        onCompose(build(SERIAL_PLACEHOLDER));
        return;
      }
      setSerialError(true);
      serialInputRef.current?.focus();
      return;
    }
    setSerialError(false);
    onCompose(build(t));
  }

  const pillRow = (
    <ul
      role="list"
      className="flex flex-wrap justify-center gap-2"
    >
      {QUICK_ACTIONS.map((a) => (
        <li key={a.id}>
          <button
            type="button"
            onClick={() => runAction(a.message)}
            className="inline-flex min-h-[2.25rem] items-center rounded-full border border-slate-200/90 bg-white px-3.5 py-1.5 text-sm font-medium text-brand-800 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:border-slate-300 hover:bg-slate-50 active:bg-slate-100/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 dark:border-brand-border dark:bg-brand-100 dark:text-brand-muted dark:hover:border-brand-border dark:hover:bg-white/10 dark:hover:text-brand-900 dark:active:bg-white/[0.08] sm:text-[0.8125rem]"
          >
            {a.label}
          </button>
        </li>
      ))}
    </ul>
  );

  if (compact) {
    return (
      <div className="w-full">
        {pillRow}
        <p className="mx-auto mt-3 max-w-md px-2 text-center text-[11px] leading-snug text-brand-muted">
          {serial.trim() ? (
            <>
              Uses saved serial{" "}
              <span className="font-mono text-brand-800 dark:text-brand-900">{serial.trim()}</span>.
            </>
          ) : (
            <>
              Tap a pill — the serial appears as{" "}
              <span className="font-mono text-brand-800 dark:text-brand-900">{SERIAL_PLACEHOLDER}</span>{" "}
              for you to fill in.
            </>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="w-full">
      {/* Desktop: slim serial input + pill row, no section headers or tips. */}
      <div
        className={[
          "mx-auto flex max-w-md items-center gap-2 rounded-full border bg-white px-3 py-1.5 shadow-sm transition-colors dark:border-brand-border dark:bg-brand-100",
          serialError
            ? "border-amber-300 ring-2 ring-amber-100"
            : "border-slate-200/90 dark:border-brand-border",
        ].join(" ")}
      >
        <label
          htmlFor={serialId}
          className="shrink-0 text-xs font-medium text-brand-muted"
        >
          Serial
        </label>
        <input
          id={serialId}
          ref={serialInputRef}
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. BB8100015261"
          value={serial}
          onChange={(e) => {
            setSerial(e.target.value);
            setSerialError(false);
          }}
          className="min-w-0 flex-1 bg-transparent text-sm text-brand-900 outline-none placeholder:text-brand-muted/45"
          inputMode="text"
        />
      </div>
      {serialError && (
        <p className="mt-1.5 text-center text-xs font-medium text-amber-800 dark:text-amber-200" role="status">
          Add a serial to use a suggestion.
        </p>
      )}

      <div className="mt-4">{pillRow}</div>
    </div>
  );
}
