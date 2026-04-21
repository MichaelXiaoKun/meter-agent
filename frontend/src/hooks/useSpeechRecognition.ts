/**
 * useSpeechRecognition
 * --------------------
 * Thin React wrapper around the browser's ``SpeechRecognition`` API
 * (vendor-prefixed ``webkitSpeechRecognition`` on WebKit-derived engines).
 * Exposes the bits a chat composer actually needs:
 *
 *   • ``usable`` — ``SpeechRecognition`` exists *and* the document is a
 *                  **secure context** (HTTPS / localhost). Plain
 *                  ``http://192.168…`` on a phone is *not* usable — the
 *                  browser blocks the mic. ``blockReason`` explains why.
d *   • ``listening`` — currently capturing audio.
 *   • ``interim``   — best-guess transcript for the *current* utterance.
 *                     Updates continuously while the user is speaking.
 *                     Empty between utterances.
 *   • ``finalText`` — concatenation of all segments the recogniser has
 *                     promoted to ``isFinal`` since the current session
 *                     started. Reset to ``""`` on every :func:`start`.
 *   • ``error``     — last non-``no-speech`` error code from the
 *                     recogniser, or ``null``. Cleared on each
 *                     :func:`start`.
 *   • ``start(lang?)`` / ``stop()`` — imperative controls. ``start`` auto-
 *                                     requests mic permission on first call.
 *
 * Design notes
 * ~~~~~~~~~~~~
 * 1. ``continuous = true`` + ``interimResults = true`` because chat users
 *    want long-form dictation with live feedback, not single-phrase
 *    recognition.
 * 2. The language defaults to ``navigator.language`` (e.g. ``zh-CN`` on a
 *    Chinese browser, ``en-US`` on a US browser). Callers can override per
 *    :func:`start` call — useful for a future language toggle.
 * 3. We intentionally ignore ``"no-speech"`` errors from the recogniser:
 *    they fire whenever the user pauses and add nothing the UI can act on.
 * 4. Some WebKit builds (esp. iOS Safari) auto-end the session after a
 *    short silence even with ``continuous = true``. We respect the
 *    ``onend`` event rather than trying to fight the browser — if the
 *    user wants to keep dictating they just tap the mic again.
 */

import { useCallback, useEffect, useRef, useState } from "react";

type AnySpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onstart: ((this: AnySpeechRecognition, ev: Event) => void) | null;
  onend: ((this: AnySpeechRecognition, ev: Event) => void) | null;
  onerror:
    | ((this: AnySpeechRecognition, ev: { error: string }) => void)
    | null;
  onresult:
    | ((
        this: AnySpeechRecognition,
        ev: {
          resultIndex: number;
          results: {
            length: number;
            [i: number]: {
              isFinal: boolean;
              [0]: { transcript: string };
            };
          };
        },
      ) => void)
    | null;
};

type SpeechRecognitionCtor = new () => AnySpeechRecognition;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/**
 * Web Speech + microphone are restricted to a **secure context** (HTTPS,
 * ``http://localhost``, ``http://127.0.0.1``, etc.). Phones that load the
 * Vite dev server as ``http://192.168.x.x:5173`` are *not* secure — the API
 * may exist but ``start()`` fails or never receives audio. We surface that
 * explicitly so it is not mistaken for a random bug.
 */
function computeVoiceUsable(): {
  usable: boolean;
  blockReason: string | null;
} {
  if (typeof window === "undefined") {
    return { usable: false, blockReason: null };
  }
  const ctor = getRecognitionCtor();
  if (!ctor) {
    return {
      usable: false,
      blockReason: "Voice input is not supported in this browser.",
    };
  }
  if (window.isSecureContext === false) {
    return {
      usable: false,
      blockReason:
        "Voice needs HTTPS or localhost. A phone link like http://192.168… is not secure, so the browser blocks the mic.",
    };
  }
  return { usable: true, blockReason: null };
}

/** iPhone / iPad / iPod; includes iPadOS desktop UA + touch Mac. */
function isIOSDevice(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    /iP(hone|od|ad)/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

export interface UseSpeechRecognitionResult {
  /** ``SpeechRecognition`` exists *and* the page is a secure context. */
  usable: boolean;
  /** Non-null when the mic cannot work until the user changes browser / URL. */
  blockReason: string | null;
  listening: boolean;
  interim: string;
  finalText: string;
  error: string | null;
  start: (lang?: string) => Promise<void>;
  stop: () => void;
}

export function useSpeechRecognition(): UseSpeechRecognitionResult {
  const initial = computeVoiceUsable();
  const ctorRef = useRef<SpeechRecognitionCtor | null>(getRecognitionCtor());
  const recognitionRef = useRef<AnySpeechRecognition | null>(null);

  const [usable] = useState<boolean>(() => initial.usable);
  const [blockReason] = useState<string | null>(() => initial.blockReason);
  const [listening, setListening] = useState<boolean>(false);
  const [interim, setInterim] = useState<string>("");
  const [finalText, setFinalText] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      try {
        recognitionRef.current?.abort();
      } catch {
        /* ignore — some engines throw if abort is called while not running */
      }
      recognitionRef.current = null;
    };
  }, []);

  const stop = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      /* already stopped */
    }
  }, []);

  const start = useCallback(async (lang?: string) => {
    const Ctor = ctorRef.current;
    if (!Ctor || !usable) return;
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      return;
    }
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch {
        /* ignore */
      }
      recognitionRef.current = null;
    }

    const rec = new Ctor();
    /*
     * iOS WebKit is flaky with ``continuous: true`` — sessions often end
     * after the first pause or never deliver ``onresult``. Single-phrase
     * mode is less convenient for long dictation but reliably produces
     * transcripts; the user can tap the mic again for the next sentence.
     */
    rec.continuous = !isIOSDevice();
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.lang =
      lang ??
      (typeof navigator !== "undefined" ? navigator.language : undefined) ??
      "en-US";

    rec.onstart = () => {
      setError(null);
      setInterim("");
      setFinalText("");
      setListening(true);
    };
    rec.onend = () => {
      setListening(false);
      setInterim("");
      recognitionRef.current = null;
    };
    rec.onerror = (ev) => {
      if (ev.error === "no-speech" || ev.error === "aborted") return;
      const code = ev.error || "unknown";
      const friendly: Record<string, string> = {
        "not-allowed":
          "Microphone or speech recognition was blocked — check site permissions in browser settings.",
        "service-not-allowed":
          "Speech recognition is turned off or not allowed for this page.",
        network:
          "Speech recognition needs a network connection (cloud transcription).",
        "audio-capture": "No microphone was found or it could not be opened.",
      };
      setError(friendly[code] ?? code);
    };
    rec.onresult = (ev) => {
      let interimText = "";
      let appended = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const result = ev.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) appended += transcript;
        else interimText += transcript;
      }
      if (appended) setFinalText((prev) => prev + appended);
      setInterim(interimText);
    };

    recognitionRef.current = rec;
    try {
      rec.start();
    } catch (e) {
      // `start()` throws "InvalidStateError" if called twice in a row
      // before the previous session ended. Surface as an error so the
      // UI can reset instead of leaving a half-started session.
      setError(e instanceof Error ? e.name : "start-failed");
      setListening(false);
      recognitionRef.current = null;
    }
  }, [usable]);

  return { usable, blockReason, listening, interim, finalText, error, start, stop };
}
