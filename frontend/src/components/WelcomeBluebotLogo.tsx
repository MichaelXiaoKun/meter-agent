/**
 * WelcomeBluebotLogo
 * -------------------
 * ``welcome-bluebot-close.png`` as the face, plus **vector-style DOM** eyes
 * (``scaleY`` blink). Starts **awake**; after **1 minute** without pointer /
 * key / wheel activity, switches to **sleep** (curved lids + ``Zz``). Any
 * activity wakes immediately. Styled in ``index.css`` via ``.welcome-bluebot--sleeping``.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const SRC_FACE = `${import.meta.env.BASE_URL}welcome-bluebot-close.png`;

/** No user activity for this long → sleep overlay (ms). */
const IDLE_TO_SLEEP_MS = 60_000;

interface WelcomeBluebotLogoProps {
  /** Square edge length in px. */
  size?: number;
  className?: string;
}

export default function WelcomeBluebotLogo({
  size = 88,
  className,
}: WelcomeBluebotLogoProps) {
  const [isSleeping, setIsSleeping] = useState(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetIdleTimer = useCallback(() => {
    setIsSleeping(false);
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current);
    }
    idleTimerRef.current = setTimeout(() => {
      idleTimerRef.current = null;
      setIsSleeping(true);
    }, IDLE_TO_SLEEP_MS);
  }, []);

  useEffect(() => {
    resetIdleTimer();
    const onActivity = () => {
      resetIdleTimer();
    };
    const opts: AddEventListenerOptions = { capture: true, passive: true };
    const events: (keyof WindowEventMap)[] = [
      "mousemove",
      "mousedown",
      "keydown",
      "touchstart",
      "wheel",
    ];
    for (const ev of events) {
      window.addEventListener(ev, onActivity, opts);
    }
    return () => {
      if (idleTimerRef.current !== null) {
        clearTimeout(idleTimerRef.current);
      }
      for (const ev of events) {
        window.removeEventListener(ev, onActivity, opts);
      }
    };
  }, [resetIdleTimer]);

  return (
    <div
      className={[
        "welcome-logo-wrap welcome-bluebot-wrap shrink-0 select-none",
        className ?? "",
      ].join(" ")}
      aria-hidden="true"
    >
      <div className="welcome-logo-bob">
        <div className="welcome-logo-breathe">
          <div
            className={[
              "welcome-bluebot-frames relative inline-block overflow-hidden rounded-[1.25rem]",
              isSleeping ? "welcome-bluebot--sleeping" : "",
            ].join(" ")}
            style={{ width: size, height: size }}
          >
            <div className="welcome-bluebot-wise-swing pointer-events-none absolute inset-0 z-0">
              <div className="welcome-bluebot-mood-surface absolute inset-0">
                <img
                  src={SRC_FACE}
                  alt=""
                  width={size}
                  height={size}
                  draggable={false}
                  className="welcome-logo-img welcome-bluebot-img pointer-events-none absolute inset-0 z-0 block h-full w-full rounded-[1.25rem] object-cover"
                />
                <div className="welcome-bluebot-face pointer-events-none absolute inset-0 z-[1]">
                  {/* Mood layers — visibility keyed to ``welcome-bluebot-mood-*`` in CSS */}
                  <div className="welcome-bluebot-mood welcome-bluebot-mood--sleep">
                    <span className="welcome-bluebot-zzz welcome-bluebot-zzz--sleep">Zz</span>
                  </div>

                  <span className="welcome-bluebot-eye-slot welcome-bluebot-eye-slot--l">
                    <span className="welcome-bluebot-eye-line" />
                    <span className="welcome-bluebot-eye" />
                  </span>
                  <span className="welcome-bluebot-eye-slot welcome-bluebot-eye-slot--r">
                    <span className="welcome-bluebot-eye-line" />
                    <span className="welcome-bluebot-eye" />
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
