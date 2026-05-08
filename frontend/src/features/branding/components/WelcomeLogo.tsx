/**
 * WelcomeLogo
 * -----------
 * Animated presentation of the bluebot company logo (served from the
 * orchestrator's ``/api/logo`` endpoint) used on the welcome screen above
 * the "What can I help with?" heading.
 *
 * The logo itself is a static raster asset (``bluebot.jpg``) so the
 * "animation" is supplied by four coordinated CSS layers that each own a
 * single transform / filter channel — they compose without fighting:
 *
 *   • ``.welcome-logo-wrap``  — one-shot **pop-in** on mount + persistent
 *                                blue halo **glow pulse** (drop-shadow).
 *   • ``.welcome-logo-bob``   — continuous gentle **vertical bob**.
 *   • ``.welcome-logo-breathe`` — continuous slow **scale breathe**.
 *   • ``img``                 — rounded corners + subtle ring.
 *
 * ``prefers-reduced-motion`` disables the motion-based layers; the logo is
 * still drawn in its final pose with the static ring so accessibility
 * isn't sacrificed for polish.
 */

interface WelcomeLogoProps {
  /** Rendered pixel size (width === height). Default 88 px. */
  size?: number;
  className?: string;
}

export default function WelcomeLogo({ size = 88, className }: WelcomeLogoProps) {
  return (
    <div
      className={[
        "welcome-logo-wrap shrink-0 select-none",
        className ?? "",
      ].join(" ")}
      aria-hidden="true"
    >
      <div className="welcome-logo-bob">
        <div className="welcome-logo-breathe">
          <img
            src="/api/logo"
            alt=""
            width={size}
            height={size}
            className="welcome-logo-img block"
            style={{ width: size, height: size }}
            draggable={false}
          />
        </div>
      </div>
    </div>
  );
}
