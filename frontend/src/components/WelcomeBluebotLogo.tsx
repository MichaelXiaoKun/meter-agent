/**
 * WelcomeBluebotLogo
 * -------------------
 * ``welcome-bluebot-close.png`` as the face, plus **vector-style DOM** eyes
 * (``scaleY`` blink). The logo can respond to light interaction and a few
 * product states without needing additional raster assets. Starts **awake**;
 * while idle, after **1 minute** without activity, switches to **sleep**
 * (curved lids + ``Zz``). Any activity wakes immediately.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

const SRC_FACE = `${import.meta.env.BASE_URL}welcome-bluebot-close.png`;

/** No user activity for this long → sleep overlay (ms). */
const IDLE_TO_SLEEP_MS = 60_000;
/** Light "getting bored" expression before full sleep (ms). */
const IDLE_TO_BORED_MS = 18_000;
/** Short decorative acknowledgement after the logo is clicked/tapped (ms). */
const POKE_FEEDBACK_MS = 900;

export type WelcomeBluebotMood = "idle" | "drafting" | "listening" | "loading";
export type WelcomeBluebotExpression = "neutral" | "happy" | "confused" | "annoyed";

interface WelcomeBluebotLogoProps {
  /** Square edge length in px. */
  size?: number;
  className?: string;
  /** Visual state controlled by the surrounding UI. */
  mood?: WelcomeBluebotMood;
  /** Lightweight expression layered on top of the product state. */
  expression?: WelcomeBluebotExpression;
  /** Enable local hover / pointer-follow behavior. */
  interactive?: boolean;
  /** Idle sleep timeout; set ``null`` to keep the logo awake. */
  sleepAfterMs?: number | null;
}

export default function WelcomeBluebotLogo({
  size = 88,
  className,
  mood = "idle",
  expression = "neutral",
  interactive = true,
  sleepAfterMs = IDLE_TO_SLEEP_MS,
}: WelcomeBluebotLogoProps) {
  const [isSleeping, setIsSleeping] = useState(false);
  const [isBored, setIsBored] = useState(false);
  const [isPoked, setIsPoked] = useState(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boredTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pokeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const canSleep = mood === "idle" && sleepAfterMs !== null;
  const effectiveExpression =
    isBored && expression === "neutral" && !isSleeping ? "bored" : expression;

  const clearRestTimers = useCallback(() => {
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (boredTimerRef.current !== null) {
      clearTimeout(boredTimerRef.current);
      boredTimerRef.current = null;
    }
  }, []);

  const clearPokeTimer = useCallback(() => {
    if (pokeTimerRef.current !== null) {
      clearTimeout(pokeTimerRef.current);
      pokeTimerRef.current = null;
    }
  }, []);

  const resetIdleTimer = useCallback(() => {
    setIsSleeping(false);
    setIsBored(false);
    clearRestTimers();
    if (!canSleep || sleepAfterMs === null) return;
    if (sleepAfterMs > IDLE_TO_BORED_MS) {
      boredTimerRef.current = setTimeout(() => {
        boredTimerRef.current = null;
        setIsBored(true);
      }, IDLE_TO_BORED_MS);
    }
    idleTimerRef.current = setTimeout(() => {
      idleTimerRef.current = null;
      if (boredTimerRef.current !== null) {
        clearTimeout(boredTimerRef.current);
        boredTimerRef.current = null;
      }
      setIsSleeping(true);
    }, sleepAfterMs);
  }, [canSleep, clearRestTimers, sleepAfterMs]);

  useEffect(() => {
    return clearPokeTimer;
  }, [clearPokeTimer]);

  useEffect(() => {
    if (interactive) return;
    clearPokeTimer();
    setIsPoked(false);
  }, [clearPokeTimer, interactive]);

  useEffect(() => {
    if (!canSleep) {
      clearRestTimers();
      setIsSleeping(false);
      setIsBored(false);
      return clearRestTimers;
    }

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
      clearRestTimers();
      for (const ev of events) {
        window.removeEventListener(ev, onActivity, opts);
      }
    };
  }, [canSleep, clearRestTimers, resetIdleTimer]);

  const setPointerPose = useCallback(
    (x: number, y: number, lifted: boolean) => {
      const el = frameRef.current;
      if (!el) return;
      el.style.setProperty("--bb-gaze-x", `${(x * 2.35).toFixed(2)}px`);
      el.style.setProperty("--bb-gaze-y", `${(y * 1.65).toFixed(2)}px`);
      el.style.setProperty("--bb-tilt", `${(x * 1.15).toFixed(2)}deg`);
      el.style.setProperty("--bb-lift-x", `${(x * 0.65).toFixed(2)}px`);
      el.style.setProperty("--bb-lift-y", lifted ? "-1.1px" : "0px");
    },
    [],
  );

  const resetPointerPose = useCallback(() => {
    setPointerPose(0, 0, false);
  }, [setPointerPose]);

  const handlePointerEnter = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!interactive || event.pointerType === "touch") return;
      resetIdleTimer();
      setPointerPose(0, -0.18, true);
    },
    [interactive, resetIdleTimer, setPointerPose],
  );

  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!interactive || event.pointerType === "touch") return;
      const el = frameRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const clamp = (value: number) => Math.max(-1, Math.min(1, value));
      const x = clamp(((event.clientX - rect.left) / rect.width - 0.5) * 2);
      const y = clamp(((event.clientY - rect.top) / rect.height - 0.5) * 2);
      setPointerPose(x, y, true);
    },
    [interactive, setPointerPose],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!interactive) return;
      event.preventDefault();
      resetIdleTimer();
      clearPokeTimer();
      setIsPoked(true);
      pokeTimerRef.current = setTimeout(() => {
        pokeTimerRef.current = null;
        setIsPoked(false);
      }, POKE_FEEDBACK_MS);
    },
    [clearPokeTimer, interactive, resetIdleTimer],
  );

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
            ref={frameRef}
            className={[
              "welcome-bluebot-frames relative inline-block overflow-hidden rounded-[1.25rem]",
              `welcome-bluebot--mood-${mood}`,
              `welcome-bluebot--expression-${effectiveExpression}`,
              interactive ? "welcome-bluebot--interactive" : "",
              isPoked ? "welcome-bluebot--poked" : "",
              isSleeping ? "welcome-bluebot--sleeping" : "",
            ].join(" ")}
            onPointerEnter={handlePointerEnter}
            onPointerMove={handlePointerMove}
            onPointerDown={handlePointerDown}
            onPointerLeave={resetPointerPose}
            onPointerCancel={resetPointerPose}
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
                  <div className="welcome-bluebot-mood welcome-bluebot-mood--listening">
                    <span className="welcome-bluebot-listen-ring welcome-bluebot-listen-ring--a" />
                    <span className="welcome-bluebot-listen-ring welcome-bluebot-listen-ring--b" />
                    <span className="welcome-bluebot-status-light" />
                  </div>
                  <div className="welcome-bluebot-mood welcome-bluebot-mood--loading">
                    <span className="welcome-bluebot-loading-dot welcome-bluebot-loading-dot--a" />
                    <span className="welcome-bluebot-loading-dot welcome-bluebot-loading-dot--b" />
                    <span className="welcome-bluebot-loading-dot welcome-bluebot-loading-dot--c" />
                  </div>
                  <span className="welcome-bluebot-eye-emote welcome-bluebot-eye-emote--sweat" />

                  <span className="welcome-bluebot-eye-slot welcome-bluebot-eye-slot--l">
                    <span className="welcome-bluebot-eye-line" />
                    <span className="welcome-bluebot-eye">
                      <span className="welcome-bluebot-eye-shine" />
                      <span className="welcome-bluebot-eye-lid" />
                    </span>
                  </span>
                  <span className="welcome-bluebot-eye-slot welcome-bluebot-eye-slot--r">
                    <span className="welcome-bluebot-eye-line" />
                    <span className="welcome-bluebot-eye">
                      <span className="welcome-bluebot-eye-shine" />
                      <span className="welcome-bluebot-eye-lid" />
                    </span>
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
