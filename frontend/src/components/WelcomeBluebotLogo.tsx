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
const SEND_ACK_FEEDBACK_MS = 720;
/** Rapid pokes make the logo briefly irritated. */
const MULTI_POKE_THRESHOLD = 3;
const MULTI_POKE_WINDOW_MS = 1_100;
const OVERPOKE_FEEDBACK_MS = 1_800;
/** Pointer loops around the face this much before the logo gets dizzy. */
const DIZZY_ROTATION_THRESHOLD_RAD = Math.PI * 2 * 1.8;
/** The loop gesture has to stay lively; slow drifting should not trigger it. */
const DIZZY_ROTATION_WINDOW_MS = 2_800;
const DIZZY_ROTATION_GAP_MS = 520;
const DIZZY_MIN_RADIUS = 0.28;
const DIZZY_FEEDBACK_MS = 2_400;
const LOADING_TO_TIRED_MS = 8_000;

type SpinTracker = {
  lastAngle: number | null;
  startedAt: number;
  lastTime: number;
  direction: -1 | 0 | 1;
  totalAngle: number;
};

const freshSpinTracker = (): SpinTracker => ({
  lastAngle: null,
  startedAt: 0,
  lastTime: 0,
  direction: 0,
  totalAngle: 0,
});

type PokeTracker = {
  count: number;
  firstAt: number;
};

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
  /** Increment this value to make the logo blink in acknowledgement. */
  acknowledgeSignal?: number;
}

export default function WelcomeBluebotLogo({
  size = 88,
  className,
  mood = "idle",
  expression = "neutral",
  interactive = true,
  sleepAfterMs = IDLE_TO_SLEEP_MS,
  acknowledgeSignal = 0,
}: WelcomeBluebotLogoProps) {
  const [isSleeping, setIsSleeping] = useState(false);
  const [isBored, setIsBored] = useState(false);
  const [isPoked, setIsPoked] = useState(false);
  const [isDizzy, setIsDizzy] = useState(false);
  const [isAcknowledging, setIsAcknowledging] = useState(false);
  const [isOverpoked, setIsOverpoked] = useState(false);
  const [isLoadingTired, setIsLoadingTired] = useState(false);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boredTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pokeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dizzyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const acknowledgeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const overpokeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadingTiredTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAcknowledgeSignalRef = useRef(acknowledgeSignal);
  const pokeTrackerRef = useRef<PokeTracker>({ count: 0, firstAt: 0 });
  const spinTrackerRef = useRef<SpinTracker>(freshSpinTracker());
  const frameRef = useRef<HTMLDivElement | null>(null);
  const canSleep = mood === "idle" && sleepAfterMs !== null;
  const effectiveExpression =
    isOverpoked
      ? "annoyed"
      : isBored && expression === "neutral" && !isSleeping && !isDizzy
        ? "bored"
        : expression;

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

  const clearDizzyTimer = useCallback(() => {
    if (dizzyTimerRef.current !== null) {
      clearTimeout(dizzyTimerRef.current);
      dizzyTimerRef.current = null;
    }
  }, []);

  const clearAcknowledgeTimer = useCallback(() => {
    if (acknowledgeTimerRef.current !== null) {
      clearTimeout(acknowledgeTimerRef.current);
      acknowledgeTimerRef.current = null;
    }
  }, []);

  const clearOverpokeTimer = useCallback(() => {
    if (overpokeTimerRef.current !== null) {
      clearTimeout(overpokeTimerRef.current);
      overpokeTimerRef.current = null;
    }
  }, []);

  const clearLoadingTiredTimer = useCallback(() => {
    if (loadingTiredTimerRef.current !== null) {
      clearTimeout(loadingTiredTimerRef.current);
      loadingTiredTimerRef.current = null;
    }
  }, []);

  const resetPokeTracker = useCallback(() => {
    pokeTrackerRef.current = { count: 0, firstAt: 0 };
  }, []);

  const resetSpinTracker = useCallback(() => {
    spinTrackerRef.current = freshSpinTracker();
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
    return () => {
      clearPokeTimer();
      clearDizzyTimer();
      clearAcknowledgeTimer();
      clearOverpokeTimer();
      clearLoadingTiredTimer();
    };
  }, [
    clearAcknowledgeTimer,
    clearDizzyTimer,
    clearLoadingTiredTimer,
    clearOverpokeTimer,
    clearPokeTimer,
  ]);

  useEffect(() => {
    if (interactive) return;
    clearPokeTimer();
    clearDizzyTimer();
    clearOverpokeTimer();
    setIsPoked(false);
    setIsDizzy(false);
    setIsOverpoked(false);
    resetPokeTracker();
    resetSpinTracker();
  }, [
    clearDizzyTimer,
    clearOverpokeTimer,
    clearPokeTimer,
    interactive,
    resetPokeTracker,
    resetSpinTracker,
  ]);

  useEffect(() => {
    clearLoadingTiredTimer();
    setIsLoadingTired(false);
    if (mood !== "loading") return clearLoadingTiredTimer;
    loadingTiredTimerRef.current = setTimeout(() => {
      loadingTiredTimerRef.current = null;
      setIsLoadingTired(true);
    }, LOADING_TO_TIRED_MS);
    return clearLoadingTiredTimer;
  }, [clearLoadingTiredTimer, mood]);

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

  const triggerAcknowledge = useCallback(() => {
    if (isDizzy || isOverpoked) return;
    clearAcknowledgeTimer();
    setIsSleeping(false);
    setIsBored(false);
    setIsAcknowledging(true);
    acknowledgeTimerRef.current = setTimeout(() => {
      acknowledgeTimerRef.current = null;
      setIsAcknowledging(false);
      resetIdleTimer();
    }, SEND_ACK_FEEDBACK_MS);
  }, [clearAcknowledgeTimer, isDizzy, isOverpoked, resetIdleTimer]);

  useEffect(() => {
    if (acknowledgeSignal === lastAcknowledgeSignalRef.current) return;
    lastAcknowledgeSignalRef.current = acknowledgeSignal;
    triggerAcknowledge();
  }, [acknowledgeSignal, triggerAcknowledge]);

  const triggerOverpoke = useCallback(() => {
    resetPokeTracker();
    resetSpinTracker();
    clearPokeTimer();
    clearDizzyTimer();
    clearOverpokeTimer();
    setIsPoked(false);
    setIsDizzy(false);
    setIsSleeping(false);
    setIsBored(false);
    setIsOverpoked(true);
    overpokeTimerRef.current = setTimeout(() => {
      overpokeTimerRef.current = null;
      setIsOverpoked(false);
      resetPointerPose();
      resetIdleTimer();
    }, OVERPOKE_FEEDBACK_MS);
  }, [
    clearDizzyTimer,
    clearOverpokeTimer,
    clearPokeTimer,
    resetIdleTimer,
    resetPointerPose,
    resetPokeTracker,
    resetSpinTracker,
  ]);

  const triggerDizzy = useCallback(() => {
    resetSpinTracker();
    clearAcknowledgeTimer();
    clearDizzyTimer();
    clearOverpokeTimer();
    clearPokeTimer();
    setIsAcknowledging(false);
    setIsOverpoked(false);
    setIsPoked(false);
    setIsSleeping(false);
    setIsBored(false);
    setIsDizzy(true);
    dizzyTimerRef.current = setTimeout(() => {
      dizzyTimerRef.current = null;
      setIsDizzy(false);
      resetPointerPose();
      resetIdleTimer();
    }, DIZZY_FEEDBACK_MS);
  }, [
    clearAcknowledgeTimer,
    clearDizzyTimer,
    clearOverpokeTimer,
    clearPokeTimer,
    resetIdleTimer,
    resetPointerPose,
    resetSpinTracker,
  ]);

  const trackPointerSpin = useCallback(
    (x: number, y: number) => {
      if (isDizzy || isOverpoked) return;
      const radius = Math.hypot(x, y);
      if (radius < DIZZY_MIN_RADIUS) {
        resetSpinTracker();
        return;
      }

      const now = performance.now();
      const angle = Math.atan2(y, x);
      const tracker = spinTrackerRef.current;
      const shouldStartFresh =
        tracker.lastAngle === null ||
        now - tracker.lastTime > DIZZY_ROTATION_GAP_MS ||
        now - tracker.startedAt > DIZZY_ROTATION_WINDOW_MS;

      if (shouldStartFresh) {
        spinTrackerRef.current = {
          lastAngle: angle,
          startedAt: now,
          lastTime: now,
          direction: 0,
          totalAngle: 0,
        };
        return;
      }

      const lastAngle = tracker.lastAngle;
      if (lastAngle === null) return;

      let delta = angle - lastAngle;
      if (delta > Math.PI) delta -= Math.PI * 2;
      if (delta < -Math.PI) delta += Math.PI * 2;
      const absDelta = Math.abs(delta);
      if (absDelta < 0.035) {
        tracker.lastAngle = angle;
        tracker.lastTime = now;
        return;
      }

      const direction = delta > 0 ? 1 : -1;
      tracker.totalAngle =
        tracker.direction !== 0 && tracker.direction !== direction
          ? absDelta
          : tracker.totalAngle + absDelta;
      tracker.direction = direction;
      tracker.lastAngle = angle;
      tracker.lastTime = now;

      if (tracker.totalAngle >= DIZZY_ROTATION_THRESHOLD_RAD) {
        triggerDizzy();
      }
    },
    [isDizzy, isOverpoked, resetSpinTracker, triggerDizzy],
  );

  const resetPointerInteraction = useCallback(() => {
    resetPointerPose();
    resetSpinTracker();
  }, [resetPointerPose, resetSpinTracker]);

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
      trackPointerSpin(x, y);
    },
    [interactive, setPointerPose, trackPointerSpin],
  );

  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!interactive) return;
      event.preventDefault();
      resetIdleTimer();
      const now = performance.now();
      const tracker = pokeTrackerRef.current;
      if (now - tracker.firstAt > MULTI_POKE_WINDOW_MS) {
        tracker.count = 0;
        tracker.firstAt = now;
      }
      tracker.count += 1;
      if (tracker.count >= MULTI_POKE_THRESHOLD) {
        triggerOverpoke();
        return;
      }
      clearPokeTimer();
      setIsPoked(true);
      pokeTimerRef.current = setTimeout(() => {
        pokeTimerRef.current = null;
        setIsPoked(false);
      }, POKE_FEEDBACK_MS);
    },
    [clearPokeTimer, interactive, resetIdleTimer, triggerOverpoke],
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
              isDizzy ? "welcome-bluebot--dizzy" : "",
              isAcknowledging ? "welcome-bluebot--acknowledging" : "",
              isOverpoked ? "welcome-bluebot--overpoked" : "",
              isLoadingTired ? "welcome-bluebot--loading-tired" : "",
            ].join(" ")}
            onPointerEnter={handlePointerEnter}
            onPointerMove={handlePointerMove}
            onPointerDown={handlePointerDown}
            onPointerLeave={resetPointerInteraction}
            onPointerCancel={resetPointerInteraction}
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
                    <span className="welcome-bluebot-eye-cross" />
                    <span className="welcome-bluebot-eye">
                      <span className="welcome-bluebot-eye-shine" />
                      <span className="welcome-bluebot-eye-lid" />
                    </span>
                  </span>
                  <span className="welcome-bluebot-eye-slot welcome-bluebot-eye-slot--r">
                    <span className="welcome-bluebot-eye-line" />
                    <span className="welcome-bluebot-eye-cross" />
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
