/**
 * In-app auth sub-views (no react-router) — keep URLs aligned with
 * ``bluebot-saas-client`` (`/forget-pass`, `/check-mail`) via the hash
 * so static hosting still serves `index.html`.
 */
export type AuthGateView = "login" | "forgot" | "check-mail";

function normalizeHashPath(): string {
  const raw = (typeof window !== "undefined" ? window.location.hash : "") || "";
  const s = raw.replace(/^#/, "").trim();
  if (!s) return "/";
  return s.startsWith("/") ? s : `/${s}`;
}

export function getAuthViewFromHash(): AuthGateView {
  const p = normalizeHashPath();
  if (p === "/forget-pass") return "forgot";
  if (p === "/check-mail") return "check-mail";
  return "login";
}

export function setHashForAuthView(view: AuthGateView) {
  if (typeof window === "undefined") return;
  if (view === "login") {
    const { pathname, search } = window.location;
    window.history.replaceState(null, "", `${pathname}${search}`);
  } else if (view === "forgot") {
    window.location.hash = "#/forget-pass";
  } else {
    window.location.hash = "#/check-mail";
  }
}
