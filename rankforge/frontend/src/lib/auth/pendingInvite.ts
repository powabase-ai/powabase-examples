/**
 * A teammate-invite token stashed while signed out so it survives the sign-in /
 * sign-up round-trip. Two guards keep a stale token from hijacking the next login
 * on a shared/kiosk browser:
 *   - consume-once: `takePendingInvite()` removes the token as it reads it, so it can
 *     only steer ONE post-auth redirect, never a later user's login.
 *   - TTL: a token older than PENDING_TTL_MS is ignored (and cleared).
 *
 * The accept link itself always carries the token in the URL — this store only
 * bridges the login round-trip, so consuming eagerly is safe.
 */
import { PENDING_INVITE_KEY } from "@/lib/constants";

const PENDING_TTL_MS = 30 * 60 * 1000; // 30 minutes

export function stashPendingInvite(token: string): void {
  try {
    localStorage.setItem(
      PENDING_INVITE_KEY,
      JSON.stringify({ token, at: Date.now() })
    );
  } catch {
    /* storage unavailable (private mode / SSR) — the URL token still works */
  }
}

export function clearPendingInvite(): void {
  try {
    localStorage.removeItem(PENDING_INVITE_KEY);
  } catch {
    /* ignore */
  }
}

/** Read AND remove the stashed token (consume-once). Returns null if absent,
 *  malformed, or older than the TTL. */
export function takePendingInvite(): string | null {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(PENDING_INVITE_KEY);
    if (raw) localStorage.removeItem(PENDING_INVITE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const { token, at } = JSON.parse(raw) as { token?: string; at?: number };
    if (!token || typeof at !== "number" || Date.now() - at > PENDING_TTL_MS) {
      return null;
    }
    return token;
  } catch {
    return null;
  }
}
