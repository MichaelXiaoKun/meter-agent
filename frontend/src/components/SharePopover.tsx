import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { toDataURL as createQrDataUrl } from "qrcode";
import { createShare, revokeShare } from "../api";
import type { PublicShareToken } from "../api";
import { exportTranscriptToPdf } from "../utils/pdfExport";
import { stripTurnActivityBlocks } from "../utils/messageStrip";
import type { Message } from "../types";

function ShareIcon({ className }: { className?: string }) {
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
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <path d="M8.59 13.51l6.83 3.98M15.42 6.49l-6.82 3.98" />
    </svg>
  );
}

export interface SharePopoverProps {
  conversationId: string;
  userId?: string;
  accessToken?: string;
  /** Sidebar / thread title for PDF filename. */
  conversationTitle: string;
  messages: Message[];
  onToast: (a: { kind: "success" | "error"; title: string; message?: string }) => void;
  createShareLink?: (conversationId: string) => Promise<PublicShareToken | string>;
  revokeShareLink?: (token: string, revokeKey?: string) => Promise<void>;
  className?: string;
}

interface MenuRect {
  top: number;
  left: number;
}

interface ShareInfo {
  token: string;
  revokeKey?: string;
  url: string;
  createdAt: number;
  qrDataUrl: string;
}

function shortToken(token: string) {
  if (token.length <= 12) return token;
  return `${token.slice(0, 4)}…${token.slice(-6)}`;
}

function formatRelative(timestamp: number) {
  const elapsedMs = Date.now() - timestamp;
  if (elapsedMs < 60_000) return "just now";
  const elapsedMinutes = Math.floor(elapsedMs / 60_000);
  if (elapsedMinutes < 60) {
    return `${elapsedMinutes} min${elapsedMinutes === 1 ? "" : "s"} ago`;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function copyWithTextareaFallback(text: string) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "0";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);

  const selection = document.getSelection();
  const previousRange = selection?.rangeCount ? selection.getRangeAt(0) : null;

  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, text.length);
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);

  if (previousRange && selection) {
    selection.removeAllRanges();
    selection.addRange(previousRange);
  }

  return copied;
}

/**
 * Share menu: export PDF, copy public snapshot link, optional revoke.
 *
 * The dropdown is rendered through a portal so that it is never trapped under
 * sibling stacking contexts (header backdrop-blur, sticky composer, etc.).
 */
export default function SharePopover({
  conversationId,
  userId,
  accessToken,
  conversationTitle,
  messages,
  onToast,
  createShareLink,
  revokeShareLink,
  className,
}: SharePopoverProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"pdf" | "link" | "revoke" | null>(null);
  /** Last created share token in this browser session (enables Revoke). */
  const [sessionShare, setSessionShare] = useState<Pick<ShareInfo, "token" | "revokeKey"> | null>(null);
  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);
  const [copied, setCopied] = useState(false);
  const [menuPos, setMenuPos] = useState<MenuRect | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const shareLinkInputRef = useRef<HTMLInputElement>(null);
  const copiedResetTimerRef = useRef<number | null>(null);
  const panelId = useId();
  const close = useCallback(() => setOpen(false), []);

  /** Re-position the menu just under the trigger button. */
  const reposition = useCallback(() => {
    const btn = buttonRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const menuW = menuRef.current?.offsetWidth ?? (shareInfo ? 320 : 224);
    const left = Math.max(8, Math.min(window.innerWidth - menuW - 8, r.right - menuW));
    const top = r.bottom + 8;
    setMenuPos({ top, left });
  }, [shareInfo]);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    const frame = window.requestAnimationFrame(reposition);
    return () => window.cancelAnimationFrame(frame);
  }, [open, reposition]);

  useEffect(() => {
    if (open) return;
    setShareInfo(null);
    setCopied(false);
  }, [open]);

  useEffect(() => {
    if (!shareInfo) return;
    window.requestAnimationFrame(() => {
      shareLinkInputRef.current?.focus();
      shareLinkInputRef.current?.select();
    });
  }, [shareInfo]);

  useEffect(() => {
    return () => {
      if (copiedResetTimerRef.current !== null) {
        window.clearTimeout(copiedResetTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function onResizeOrScroll() {
      reposition();
    }
    window.addEventListener("resize", onResizeOrScroll);
    window.addEventListener("scroll", onResizeOrScroll, true);
    return () => {
      window.removeEventListener("resize", onResizeOrScroll);
      window.removeEventListener("scroll", onResizeOrScroll, true);
    };
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    function onPointerDown(e: PointerEvent) {
      const t = e.target as Node | null;
      if (!t) return;
      // Ignore clicks on the toggle button itself (it manages its own toggle).
      if (buttonRef.current && buttonRef.current.contains(t)) return;
      // Ignore clicks inside the menu panel.
      if (menuRef.current && menuRef.current.contains(t)) return;
      close();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [open, close]);

  const markCopied = useCallback(() => {
    setCopied(true);
    if (copiedResetTimerRef.current !== null) {
      window.clearTimeout(copiedResetTimerRef.current);
    }
    copiedResetTimerRef.current = window.setTimeout(() => {
      setCopied(false);
      copiedResetTimerRef.current = null;
    }, 1500);
  }, []);

  async function copyText(text: string) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        // Some local/dev contexts deny the async Clipboard API even after a user click.
        // The textarea fallback keeps the copy button useful in those browsers.
      }
    }

    if (!copyWithTextareaFallback(text)) {
      throw new Error("Clipboard access was blocked");
    }
  }

  async function handleExportPdf() {
    if (busy || messages.length === 0) return;
    setBusy("pdf");
    try {
      await exportTranscriptToPdf({
        messages: stripTurnActivityBlocks(messages),
        title: conversationTitle,
      });
      onToast({ kind: "success", title: "PDF downloaded" });
      close();
    } catch (e) {
      onToast({
        kind: "error",
        title: "Could not create PDF",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(null);
    }
  }

  async function handleCopyLink() {
    if (busy) return;
    setBusy("link");
    try {
      const created = createShareLink
        ? await createShareLink(conversationId)
        : await createShare(conversationId, userId ?? "", accessToken ?? "");
      const token = typeof created === "string" ? created : created.token;
      const revokeKey = typeof created === "string" ? undefined : created.revokeKey;
      setSessionShare({ token, revokeKey });
      const url = `${window.location.origin}${window.location.pathname}#/share/${token}`;
      const qrDataUrl = await createQrDataUrl(url, { margin: 1, width: 160 });
      setShareInfo({ token, revokeKey, url, createdAt: Date.now(), qrDataUrl });
      try {
        await copyText(url);
        markCopied();
      } catch {
        // Safari can reject clipboard writes after async work, even though the
        // share was created. Keep the flow positive and let the visible panel
        // provide a direct Copy button plus a selected URL fallback.
      }
      onToast({ kind: "success", title: "Public link ready" });
    } catch (e) {
      onToast({
        kind: "error",
        title: "Could not create share link",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(null);
    }
  }

  async function handleCopyAgain() {
    if (!shareInfo) return;
    try {
      await copyText(shareInfo.url);
      markCopied();
    } catch (e) {
      onToast({
        kind: "error",
        title: "Could not copy link",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  function handleDone() {
    close();
  }

  async function handleRevoke() {
    const currentShare = shareInfo ?? sessionShare;
    if (!currentShare?.token || busy) return;
    setBusy("revoke");
    try {
      if (revokeShareLink) {
        await revokeShareLink(currentShare.token, currentShare.revokeKey);
      } else {
        await revokeShare(currentShare.token, userId ?? "", accessToken ?? "");
      }
      setSessionShare(null);
      setShareInfo(null);
      setCopied(false);
      onToast({ kind: "success", title: "Share link revoked" });
      close();
    } catch (e) {
      onToast({
        kind: "error",
        title: "Could not revoke link",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusy(null);
    }
  }

  const menu =
    open && menuPos
      ? createPortal(
        <div
          ref={menuRef}
          id={panelId}
          role={shareInfo ? "dialog" : "menu"}
          style={{ position: "fixed", top: menuPos.top, left: menuPos.left, zIndex: 9999 }}
          aria-label={shareInfo ? "Public share link" : "Share menu"}
          className={[
            "rounded-xl border border-brand-border bg-white shadow-lg dark:border-brand-border dark:bg-brand-100",
            shareInfo ? "w-[20rem] p-4" : "min-w-[14rem] p-1",
          ].join(" ")}
        >
          {shareInfo ? (
            <div className="space-y-4">
              <header className="space-y-1">
                <h3 className="text-sm font-semibold text-brand-950 dark:text-white">
                  Public link ready
                </h3>
                <p className="text-xs leading-5 text-brand-600 dark:text-brand-muted">
                  Anyone with this link can view this snapshot. Editing is disabled.
                </p>
              </header>

              <div className="flex items-center gap-2 rounded-lg border border-brand-border bg-brand-50 p-2 dark:border-brand-border dark:bg-white/5">
                <input
                  ref={shareLinkInputRef}
                  readOnly
                  value={shareInfo.url}
                  onFocus={(e) => e.currentTarget.select()}
                  className="min-w-0 flex-1 bg-transparent font-mono text-xs text-brand-900 outline-none dark:text-brand-muted"
                  aria-label="Public share link"
                />
                <button
                  type="button"
                  onClick={handleCopyAgain}
                  className="rounded-md bg-white px-2 py-1 text-xs font-medium text-brand-800 shadow-sm ring-1 ring-brand-border/70 hover:bg-brand-100 dark:bg-brand-950 dark:text-brand-muted dark:hover:bg-white/10"
                >
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>

              <div className="flex gap-3">
                <div className="rounded-lg border border-brand-border bg-white p-2 dark:border-brand-border dark:bg-white">
                  <img
                    src={shareInfo.qrDataUrl}
                    alt="QR code for public share link"
                    className="h-32 w-32"
                  />
                </div>
                <dl className="min-w-0 flex-1 space-y-2 text-xs">
                  <div>
                    <dt className="font-medium text-brand-500 dark:text-brand-400">Created</dt>
                    <dd className="mt-0.5 text-brand-900 dark:text-brand-muted">
                      {formatRelative(shareInfo.createdAt)}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-medium text-brand-500 dark:text-brand-400">Link ID</dt>
                    <dd className="mt-0.5 truncate font-mono text-brand-900 dark:text-brand-muted">
                      {shortToken(shareInfo.token)}
                    </dd>
                  </div>
                </dl>
              </div>

              <footer className="flex items-center justify-end gap-2 border-t border-brand-border pt-3 dark:border-brand-border">
                <a
                  href={shareInfo.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-lg px-3 py-1.5 text-sm font-medium text-brand-700 hover:bg-brand-50 dark:text-brand-muted dark:hover:bg-white/10"
                >
                  Open
                </a>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={handleRevoke}
                  className="rounded-lg px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-50 disabled:opacity-50 dark:text-amber-200 dark:hover:bg-amber-950/40"
                >
                  {busy === "revoke" ? "Revoking…" : "Revoke"}
                </button>
                <button
                  type="button"
                  onClick={handleDone}
                  className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400"
                >
                  Done
                </button>
              </footer>
            </div>
          ) : (
            <>
              <button
                type="button"
                role="menuitem"
                disabled={!!busy}
                onClick={handleExportPdf}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-brand-900 hover:bg-brand-50 disabled:opacity-50 dark:text-brand-muted dark:hover:bg-white/10"
              >
                <span className="inline-block w-4 text-center">
                  {busy === "pdf" ? "…" : ""}
                </span>
                Export to PDF
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={!!busy}
                onClick={handleCopyLink}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-brand-900 hover:bg-brand-50 disabled:opacity-50 dark:text-brand-muted dark:hover:bg-white/10"
              >
                <span className="inline-block w-4 text-center">
                  {busy === "link" ? "…" : ""}
                </span>
                Copy public link
              </button>
              {sessionShare && (
                <button
                  type="button"
                  role="menuitem"
                  disabled={!!busy}
                  onClick={handleRevoke}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-amber-800 hover:bg-amber-50 disabled:opacity-50 dark:text-amber-200 dark:hover:bg-amber-950/40"
                >
                  <span className="inline-block w-4 text-center">
                    {busy === "revoke" ? "…" : ""}
                  </span>
                  Revoke last link
                </button>
              )}
            </>
          )}
        </div>,
        document.body,
      )
      : null;

  return (
    <div className={["relative shrink-0", className].filter(Boolean).join(" ")}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={panelId}
        title="Share conversation"
        className={[
          "flex h-10 w-10 items-center justify-center rounded-xl border text-brand-700 transition-colors",
          "border-brand-border/80 bg-white shadow-sm ring-1 ring-brand-border/40",
          "hover:border-brand-400 hover:bg-brand-50",
          "dark:border-brand-border dark:bg-brand-100 dark:text-brand-muted dark:hover:bg-white/10",
          open ? "ring-2 ring-brand-500/30" : "",
        ].join(" ")}
      >
        <ShareIcon className="h-5 w-5" />
        <span className="sr-only">Share</span>
      </button>
      {menu}
    </div>
  );
}
