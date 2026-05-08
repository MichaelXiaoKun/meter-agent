/**
 * WelcomeMascot
 * ---------------
 * Stylized **inline electromagnetic / ultrasonic flow meter** (flanged
 * sensing tube + transmitter housing + LCD) for the welcome screen. Kept
 * readable at favicon sizes: simple solids, brand CSS variables, no raster.
 *
 * Motion (see ``index.css``):
 *
 *   • ``.mascot-wrap``  — one-shot pop-in on mount.
 *   • ``.mascot-bob``   — gentle vertical bob on the whole graphic.
 *   • ``.mascot-upper`` — occasional tiny **nod** of the transmitter housing
 *                         (same keyframes name ``mascot-pipe-wave``, milder
 *                         angles than the old cartoon pipe lift).
 *   • ``.mascot-display`` — LCD face briefly **dims** like a refresh tick.
 */

const VIEW_W = 180;
const VIEW_H = 108;

interface WelcomeMascotProps {
  /** Rendered width in px; height follows viewBox aspect ratio. */
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
          aria-label="Stylized flow meter with flanged pipe and transmitter display"
          xmlns="http://www.w3.org/2000/svg"
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          width={size}
          height={(size * VIEW_H) / VIEW_W}
          className="mascot-svg block"
        >
          {/* Left process flange */}
          <rect
            x="8"
            y="34"
            width="30"
            height="40"
            rx="4"
            fill="var(--color-brand-500)"
            stroke="var(--color-brand-900)"
            strokeWidth="2"
          />
          <circle cx="17" cy="46" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="29" cy="46" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="17" cy="62" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="29" cy="62" r="2.2" fill="var(--color-brand-900)" />

          {/* Sensing tube (metering section) */}
          <rect
            x="34"
            y="46"
            width="112"
            height="24"
            rx="12"
            fill="var(--color-brand-500)"
            stroke="var(--color-brand-900)"
            strokeWidth="2.5"
          />
          <path
            d="M44 50 Q90 47 136 50"
            fill="none"
            stroke="#ffffff"
            strokeWidth="1.8"
            strokeLinecap="round"
            opacity="0.35"
          />

          {/* Right process flange */}
          <rect
            x="142"
            y="34"
            width="30"
            height="40"
            rx="4"
            fill="var(--color-brand-500)"
            stroke="var(--color-brand-900)"
            strokeWidth="2"
          />
          <circle cx="151" cy="46" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="163" cy="46" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="151" cy="62" r="2.2" fill="var(--color-brand-900)" />
          <circle cx="163" cy="62" r="2.2" fill="var(--color-brand-900)" />

          {/* Flow direction (process right) */}
          <path
            d="M 118 72 L 132 72 L 126 67 M 132 72 L 126 77"
            fill="none"
            stroke="var(--color-brand-900)"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity="0.85"
          />

          {/* Transmitter housing + LCD (animated nod on ``.mascot-upper``) */}
          <g className="mascot-upper">
            <rect
              x="58"
              y="6"
              width="64"
              height="44"
              rx="8"
              fill="var(--color-brand-700)"
              stroke="var(--color-brand-900)"
              strokeWidth="2"
            />
            <rect
              x="64"
              y="10"
              width="52"
              height="6"
              rx="2"
              fill="color-mix(in oklab, var(--color-brand-900) 35%, transparent)"
              opacity="0.9"
            />
            {/* Cable / conduit stub */}
            <path
              d="M 108 6 Q 112 0 116 2"
              fill="none"
              stroke="var(--color-brand-900)"
              strokeWidth="2"
              strokeLinecap="round"
            />
            <circle cx="116" cy="2" r="2.5" fill="var(--color-brand-500)" stroke="var(--color-brand-900)" strokeWidth="1.2" />

            {/* Status LED */}
            <circle cx="70" cy="19" r="2" fill="#34d399" stroke="var(--color-brand-900)" strokeWidth="0.8" />

            {/* LCD stack — opacity flicker reads like a live readout */}
            <g className="mascot-display" transform="translate(76, 24)">
              <rect
                width="28"
                height="18"
                rx="2.5"
                fill="#0a1628"
                stroke="var(--color-brand-border)"
                strokeWidth="0.9"
              />
              <rect x="4" y="4" width="20" height="2.8" rx="1" fill="#d4e4ff" opacity="0.92" />
              <rect x="4" y="8.5" width="14" height="2.6" rx="1" fill="#d4e4ff" opacity="0.88" />
              <rect x="4" y="12.5" width="18" height="2.2" rx="1" fill="#d4e4ff" opacity="0.82" />
            </g>
          </g>
        </svg>
      </div>
    </div>
  );
}
