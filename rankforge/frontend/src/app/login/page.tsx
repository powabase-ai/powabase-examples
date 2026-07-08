"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth/AuthProvider";
import { takePendingInvite } from "@/lib/auth/pendingInvite";

/** After auth, resume a pending teammate-invite accept if a token is stashed
 *  (from opening an /accept-invite link while signed out); else go home.
 *  `takePendingInvite` consumes the token (once, with a TTL) so a stale one can't
 *  steer a later user's login on a shared browser. */
function postAuthDest(): string {
  if (typeof window === "undefined") return "/";
  const token = takePendingInvite();
  return token ? `/accept-invite?token=${encodeURIComponent(token)}` : "/";
}

export default function LoginPage() {
  const { signIn, signUp, session, loading } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  // Already signed in? Leave the login page.
  useEffect(() => {
    if (!loading && session) router.replace(postAuthDest());
  }, [loading, session, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "signin") {
        await signIn(email, password);
        router.replace(postAuthDest());
      } else {
        const { needsConfirm } = await signUp(email, password);
        if (needsConfirm) {
          toast.success("Check your email to confirm, then sign in.");
          setMode("signin");
        } else {
          router.replace(postAuthDest());
        }
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <span className="font-display text-3xl font-bold tracking-tight">
            Rank<span className="text-[rgb(var(--ember))]">Forge</span>
          </span>
          <p className="mt-2 text-sm text-muted-foreground">
            Forge SEO/GEO content from live search intelligence.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-border bg-card p-6 shadow-sm"
        >
          <h1 className="mb-5 font-display text-lg font-semibold">
            {mode === "signin" ? "Sign in" : "Create your account"}
          </h1>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={
                  mode === "signin" ? "current-password" : "new-password"
                }
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
          </div>

          <Button
            type="submit"
            variant="gold"
            className="mt-6 w-full"
            disabled={busy}
          >
            {busy && <Loader2 className="animate-spin" />}
            {mode === "signin" ? "Sign in" : "Sign up"}
          </Button>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            {mode === "signin" ? "No account yet?" : "Already have an account?"}{" "}
            <button
              type="button"
              className="font-medium text-[rgb(var(--ember))] hover:underline"
              onClick={() =>
                setMode(mode === "signin" ? "signup" : "signin")
              }
            >
              {mode === "signin" ? "Create one" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
