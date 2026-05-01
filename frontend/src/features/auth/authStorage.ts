/**
 * bluebot account token storage — same keys as before, with optional
 * **session-only** sign-in (mirrors the SaaS “Keep me logged in” checkbox).
 *
 * On load, session storage wins if present so a tab-scoped session is picked
 * up before a stale local entry.
 */
const KEY_TOKEN = "bb_token";
const KEY_USER = "bb_user";

function safeGet(storage: Storage, key: string): string {
  try {
    return storage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function getStoredAuth(): { token: string; user: string } {
  if (typeof window === "undefined") {
    return { token: "", user: "" };
  }
  const tS = safeGet(sessionStorage, KEY_TOKEN);
  const uS = safeGet(sessionStorage, KEY_USER);
  if (tS || uS) {
    return { token: tS, user: uS };
  }
  return {
    token: safeGet(localStorage, KEY_TOKEN),
    user: safeGet(localStorage, KEY_USER),
  };
}

export function setAuth(
  token: string,
  user: string,
  options: { persist: boolean }
): void {
  if (typeof window === "undefined") return;
  if (!token) {
    try {
      sessionStorage.removeItem(KEY_TOKEN);
      sessionStorage.removeItem(KEY_USER);
      localStorage.removeItem(KEY_TOKEN);
      localStorage.removeItem(KEY_USER);
    } catch {
      /* ignore */
    }
    return;
  }
  try {
    if (options.persist) {
      localStorage.setItem(KEY_TOKEN, token);
      localStorage.setItem(KEY_USER, user);
      sessionStorage.removeItem(KEY_TOKEN);
      sessionStorage.removeItem(KEY_USER);
    } else {
      sessionStorage.setItem(KEY_TOKEN, token);
      sessionStorage.setItem(KEY_USER, user);
      localStorage.removeItem(KEY_TOKEN);
      localStorage.removeItem(KEY_USER);
    }
  } catch {
    /* ignore */
  }
}

export function clearAuth(): void {
  setAuth("", "", { persist: true });
}
