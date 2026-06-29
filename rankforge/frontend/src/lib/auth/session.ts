/**
 * Session store — the single source of truth for the current access token.
 *
 * Lives outside React so the plain `api.ts` fetch wrapper can read the token and
 * trigger a refresh on a 401. `AuthProvider` subscribes to keep React state in
 * sync. Persisted to localStorage so a reload keeps you signed in.
 *
 * SECURITY TRADEOFF: this is the standard Supabase/GoTrue SPA pattern, but it stores
 * the long-lived `refresh_token` in localStorage — so any XSS that can read it is a
 * *persistent* account takeover (not just a short-lived access token). For a
 * hardened deployment, prefer holding the refresh token in an httpOnly cookie
 * (refresh via a same-origin backend route) and keep only the access token in JS.
 */

import { refreshGrant, signOutRequest, type Session } from "./gotrue";

const KEY = "rankforge.session";

let current: Session | null = null;
let loaded = false;
const listeners = new Set<(s: Session | null) => void>();

function notify() {
  for (const l of listeners) l(current);
}

export function loadSession(): Session | null {
  if (loaded) return current;
  loaded = true;
  if (typeof window !== "undefined") {
    const raw = window.localStorage.getItem(KEY);
    if (raw) {
      try {
        current = JSON.parse(raw) as Session;
      } catch {
        current = null;
      }
    }
  }
  return current;
}

export function getSession(): Session | null {
  return loaded ? current : loadSession();
}

export function setSession(s: Session | null): void {
  current = s;
  loaded = true;
  if (typeof window !== "undefined") {
    if (s) window.localStorage.setItem(KEY, JSON.stringify(s));
    else window.localStorage.removeItem(KEY);
  }
  notify();
}

export function subscribe(cb: (s: Session | null) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function getAccessToken(): string | null {
  return getSession()?.access_token ?? null;
}

let refreshing: Promise<Session | null> | null = null;

/** Refresh the access token (deduped). Clears the session if refresh fails. */
export function refresh(): Promise<Session | null> {
  const s = getSession();
  if (!s?.refresh_token) return Promise.resolve(null);
  if (!refreshing) {
    refreshing = refreshGrant(s.refresh_token)
      .then((ns) => {
        setSession(ns);
        return ns;
      })
      .catch(() => {
        setSession(null);
        return null;
      })
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

export async function signOut(): Promise<void> {
  const s = getSession();
  if (s) await signOutRequest(s.access_token);
  setSession(null);
}
