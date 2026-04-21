/**
 * MicButton
 * ---------
 * Round composer affordance that toggles :hook:`useSpeechRecognition`.
 * Visual states:
 *
 *   • Idle      — brand-bordered outline, mic glyph.
 *   • Listening — filled red with a pulsing ring, mic glyph. Tap again
 *                 to stop. A thin "recording dot" pulses next to the glyph
 *                 so the state is unambiguous even without colour vision.
 *   • Error     — filled amber with an exclamation glyph for ~3 s, then
 *                 returns to idle so the user can retry. Title attribute
 *                 surfaces the raw error code for debugging.
 *   • Disabled  — greyed out (while a request is in-flight or auth is
 *                 missing).
 *
 * Intentionally minimal prop surface: the parent owns the textarea and the
 * send action; this component only *suggests* new text via ``onTranscript``.
 */

interface MicButtonProps {
  listening: boolean;
  disabled?: boolean;
  error?: string | null;
  onToggle: () => void;
  className?: string;
}

function MicIcon({ className }: { className?: string }) {
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
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <line x1="12" y1="18" x2="12" y2="22" />
      <line x1="9" y1="22" x2="15" y2="22" />
    </svg>
  );
}

function AlertIcon({ className }: { className?: string }) {
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
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="8" x2="12" y2="13" />
      <circle cx="12" cy="16.5" r="0.6" fill="currentColor" />
    </svg>
  );
}

export default function MicButton({
  listening,
  disabled,
  error,
  onToggle,
  className,
}: MicButtonProps) {
  const baseClass =
    "relative flex h-12 w-12 min-h-[48px] min-w-[48px] shrink-0 items-center justify-center rounded-full border transition-colors sm:min-h-[44px] sm:min-w-[44px]";
  const stateClass = listening
    ? "border-transparent bg-red-600 text-white shadow-[0_0_0_0_rgba(220,38,38,0.45)] mic-button-pulse"
    : error
      ? "border-amber-300 bg-amber-100 text-amber-700"
      : "border-slate-200 bg-white text-brand-800 hover:border-brand-400";
  const disabledClass = disabled ? "opacity-50 cursor-not-allowed" : "";

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={listening}
      aria-label={
        listening ? "Stop voice input" : error ? error : "Start voice input"
      }
      title={error ?? undefined}
      className={[baseClass, stateClass, disabledClass, className ?? ""]
        .filter(Boolean)
        .join(" ")}
    >
      {error && !listening ? (
        <AlertIcon className="h-5 w-5" />
      ) : (
        <MicIcon className="h-5 w-5" />
      )}
      {listening && (
        <span
          className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-white"
          aria-hidden
        />
      )}
    </button>
  );
}
