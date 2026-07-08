"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { useQueryClient } from "@tanstack/react-query";

import { accountApi, ApiError, type Profile } from "@/lib/api";
import {
  signInWithPassword,
  signUp as gtSignUp,
  updatePassword,
  type Session,
} from "./gotrue";
import {
  loadSession,
  setSession,
  signOut as sessionSignOut,
  subscribe,
} from "./session";

interface AuthValue {
  session: Session | null;
  profile: Profile | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string
  ) => Promise<{ needsConfirm: boolean }>;
  signOut: () => Promise<void>;
  /** Re-fetch the profile (e.g. after redeeming the signup invite code) so the app
   *  re-evaluates the invite gate without a full reload. */
  refreshProfile: () => Promise<void>;
  /** Change the signed-in user's password. Verifies `currentPassword` first
   *  (app-level guard), then sets the new one. Throws with a friendly message. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  // The QueryClient lives ABOVE this provider (QueryProvider wraps AuthProvider), so
  // it outlives a logout. We must wipe it on every logout path or the next user on a
  // shared browser is served the previous tenant's cached lists (brands, team roster)
  // straight from cache (global keys collide across accounts).
  const qc = useQueryClient();

  // Hydrate from storage and keep React in sync with the session store.
  useEffect(() => {
    setSessionState(loadSession());
    return subscribe((ns) => {
      setSessionState(ns);
      if (!ns) {
        setProfile(null);
        // Covers sign-out AND session-expiry / forced-logout (all route through
        // setSession(null) → this subscriber).
        qc.clear();
      }
    });
  }, [qc]);

  // Resolve the caller's profile (role) whenever the access token changes.
  const token = session?.access_token;
  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setProfile(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    accountApi
      .me()
      .then((me) => {
        if (!cancelled) setProfile(me);
      })
      .catch((e) => {
        // Only a genuinely invalid/expired token (401, after api.ts already tried a
        // refresh) should drop the session. A transient 5xx — e.g. a 503 from pool
        // exhaustion that the backend answers with Retry-After backoff — must NOT log
        // every client out at once under load.
        if (!cancelled && e instanceof ApiError && e.status === 401)
          setSession(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const signIn = useCallback(async (email: string, password: string) => {
    const s = await signInWithPassword(email, password);
    setSession(s); // notifies subscribers → state + profile fetch
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const r = await gtSignUp(email, password);
    if (r.access_token && r.refresh_token) {
      setSession(r as Session);
      return { needsConfirm: false };
    }
    return { needsConfirm: true };
  }, []);

  const signOut = useCallback(async () => {
    await sessionSignOut();
    qc.clear(); // belt-and-suspenders: drop the previous tenant's cached data
  }, [qc]);

  const refreshProfile = useCallback(async () => {
    const me = await accountApi.me();
    setProfile(me);
  }, []);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const email = session?.user?.email;
      if (!email) throw new Error("You're not signed in.");
      let fresh: Session;
      try {
        // Re-authenticate to prove the caller knows the current password. This also
        // mints a fresh session we then use for the update.
        fresh = await signInWithPassword(email, currentPassword);
      } catch {
        throw new Error("Current password is incorrect.");
      }
      await updatePassword(fresh.access_token, newPassword);
      setSession(fresh); // adopt the fresh (still-valid) session
    },
    [session]
  );

  return (
    <Ctx.Provider
      value={{
        session,
        profile,
        loading,
        signIn,
        signUp,
        signOut,
        refreshProfile,
        changePassword,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
