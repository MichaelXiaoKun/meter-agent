/**
 * WelcomeMascot
 * -------------
 * Small inline-SVG cartoon of a water meter holding a pipe, rendered above
 * the welcome-screen heading. It runs three coordinated animations that
 * each live on their own transform layer so they don't fight each other:
 *
 *   • Outer wrapper (``.mascot-wrap``)   — one-shot **pop-in** on mount.
 *   • Inner wrapper (``.mascot-bob``)    — continuous gentle **vertical bob**.
 *   • SVG group     (``.mascot-upper``)  — occasional **pipe-lift wave**.
 *
 * ``prefers-reduced-motion`` disables all three animations; the mascot is
 * still drawn in its final pose.
 *
 * The SVG itself is hand-authored from simple primitives (rounded-rect
 * body, gauge face, eye dots, curved arms, pipe cylinder, droplet) so it
 * stays crisp at any size and inherits the project's brand colors from
 * CSS custom properties — no external asset pipeline.
 */

interface WelcomeMascotProps {
  /**
   * Rendered pixel height. Default 72 px (≈ one headline tall). Consumers
   * can pass smaller/larger values for responsive tweaking; width scales
   * via the fixed viewBox aspect ratio.
   */
  size?: number;
  className?: string;
}

export default function WelcomeMascot({
  size = 72,
  className,
}: WelcomeMascotProps) {
  return (
    <div
      className={[
        "mascot-wrap shrink-0 select-none",
        className ?? "",
      ].join(" ")}
      aria-hidden="true"
    >
      <div className="mascot-bob">
        <svg
          role="img"
          aria-label="A cartoon water meter holding a pipe"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 140 120"
          width={size}
          height={(size * 120) / 140}
          className="mascot-svg block"
        >
          {/* ------------------------------------------------------------
              Static character: body, feet, face, gauge, droplet.
              Drawn first so the pipe + arms overlap it on top.
              ------------------------------------------------------------ */}

          {/* Water droplet above the head — subtle water cue. */}
          <path
            d="M70 6 C63 18, 62 26, 70 28 C78 26, 77 18, 70 6 Z"
            fill="var(--color-brand-500)"
            stroke="var(--color-brand-900)"
            strokeWidth="1.4"
            strokeLinejoin="round"
            opacity="0.9"
          />
          <path
            d="M68 12 Q66 18, 68 22"
            fill="none"
            stroke="#ffffff"
            strokeWidth="1.2"
            strokeLinecap="round"
            opacity="0.7"
          />

          {/* Feet — drawn before body so they look tucked under it. */}
          <rect
            x="50"
            y="92"
            width="12"
            height="7"
            rx="2.5"
            fill="var(--color-brand-900)"
          />
          <rect
            x="78"
            y="92"
            width="12"
            height="7"
            rx="2.5"
            fill="var(--color-brand-900)"
          />

          {/* Meter body — rounded rectangle. */}
          <rect
            x="42"
            y="38"
            width="56"
            height="56"
            rx="14"
            ry="14"
            fill="var(--color-brand-500)"
            stroke="var(--color-brand-900)"
            strokeWidth="2.5"
          />

          {/* Subtle highlight on the body for a slightly lit / wet feel. */}
          <path
            d="M48 46 Q52 42, 58 42"
            fill="none"
            stroke="#ffffff"
            strokeWidth="2.2"
            strokeLinecap="round"
            opacity="0.55"
          />

          {/* Eyes. */}
          <circle cx="58" cy="56" r="3" fill="#ffffff" />
          <circle cx="58" cy="56" r="1.4" fill="var(--color-brand-900)" />
          <circle cx="82" cy="56" r="3" fill="#ffffff" />
          <circle cx="82" cy="56" r="1.4" fill="var(--color-brand-900)" />

          {/* Gauge face — the "water meter" cue. */}
          <circle
            cx="70"
            cy="76"
            r="11"
            fill="#f5f8ff"
            stroke="var(--color-brand-900)"
            strokeWidth="1.5"
          />
          {/* Tick marks at 12/3/6/9. */}
          <line x1="70" y1="67" x2="70" y2="69" stroke="var(--color-brand-900)" strokeWidth="1" strokeLinecap="round" />
          <line x1="79" y1="76" x2="77" y2="76" stroke="var(--color-brand-900)" strokeWidth="1" strokeLinecap="round" />
          <line x1="61" y1="76" x2="63" y2="76" stroke="var(--color-brand-900)" strokeWidth="1" strokeLinecap="round" />
          <line x1="70" y1="83" x2="70" y2="85" stroke="var(--color-brand-900)" strokeWidth="1" strokeLinecap="round" />
          {/* Needle pointing roughly to the "2 o'clock" position. */}
          <line x1="70" y1="76" x2="76" y2="71" stroke="#c0392b" strokeWidth="1.8" strokeLinecap="round" />
          <circle cx="70" cy="76" r="1.6" fill="var(--color-brand-900)" />

          {/* ------------------------------------------------------------
              Upper group — pipe + arms. Rotates together during the
              periodic ``mascot-pipe-wave`` animation so the meter looks
              like it's proudly lifting the pipe overhead.
              ------------------------------------------------------------ */}
          <g className="mascot-upper">
            {/* Pipe — horizontal cylinder. */}
            <rect
              x="18"
              y="26"
              width="104"
              height="14"
              rx="7"
              ry="7"
              fill="#a7d8f0"
              stroke="var(--color-brand-900)"
              strokeWidth="2"
            />
            {/* Pipe end caps (flanges). */}
            <circle cx="18" cy="33" r="5.5" fill="var(--color-brand-500)" stroke="var(--color-brand-900)" strokeWidth="2" />
            <circle cx="122" cy="33" r="5.5" fill="var(--color-brand-500)" stroke="var(--color-brand-900)" strokeWidth="2" />
            {/* Highlight stripe on pipe — reads as light catching water/metal. */}
            <path
              d="M26 30 L114 30"
              stroke="#ffffff"
              strokeWidth="1.4"
              strokeLinecap="round"
              opacity="0.55"
            />

            {/* Left arm — from body shoulder up to the pipe. */}
            <path
              d="M48 44 Q 38 36, 30 34"
              fill="none"
              stroke="var(--color-brand-900)"
              strokeWidth="3"
              strokeLinecap="round"
            />
            {/* Right arm. */}
            <path
              d="M92 44 Q 102 36, 110 34"
              fill="none"
              stroke="var(--color-brand-900)"
              strokeWidth="3"
              strokeLinecap="round"
            />
          </g>
        </svg>
      </div>
    </div>
  );
}
