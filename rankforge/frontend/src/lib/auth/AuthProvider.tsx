"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { accountApi, type Profile } from "@/lib/api";
import { signInWithPassword, signUp as gtSignUp, type Session } from "./gotrue";
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
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  // Hydrate from storage and keep React in sync with the session store.
  useEffect(() => {
    setSessionState(loadSession());
    return subscribe((ns) => {
      setSessionState(ns);
      if (!ns) setProfile(null);
    });
  }, []);

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
      .catch(() => {
        // Token invalid/expired beyond refresh — drop the session.
        if (!cancelled) setSession(null);
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
  }, []);

  return (
    <Ctx.Provider
      value={{ session, profile, loading, signIn, signUp, signOut }}
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
