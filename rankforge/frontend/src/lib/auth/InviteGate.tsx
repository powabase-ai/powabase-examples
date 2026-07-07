"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { accountApi } from "@/lib/api";
import { useAuth } from "./AuthProvider";

/** Post-registration gate: a signed-in but unverified account must redeem the shared
 *  invite code once before the app unlocks. Rendered by RequireAuth in place of the app
 *  when `profile.invite_verified` is false. */
export function InviteGate() {
  const { profile, refreshProfile, signOut } = useAuth();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    try {
      await accountApi.redeemInvite(code.trim());
      // Success flips invite_verified; refreshing the profile lets RequireAuth
      // re-render the actual app in place.
      await refreshProfile();
      toast.success("You're in — welcome to RankForge.");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "That invite code wasn't accepted."
      );
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
            RankForge is invite-only right now. Enter your invite code to finish
            setting up your account.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-border bg-card p-6 shadow-sm"
        >
          <h1 className="mb-5 font-display text-lg font-semibold">Enter invite code</h1>

          <div className="space-y-1.5">
            <Label htmlFor="invite_code">Invite code</Label>
            <Input
              id="invite_code"
              autoComplete="off"
              autoFocus
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="your-invite-code"
            />
          </div>

          <Button
            type="submit"
            variant="gold"
            className="mt-6 w-full"
            disabled={busy || !code.trim()}
          >
            {busy && <Loader2 className="animate-spin" />}
            Unlock RankForge
          </Button>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            {profile?.email ? `Signed in as ${profile.email}. ` : ""}
            <button
              type="button"
              className="font-medium text-[rgb(var(--ember))] hover:underline"
              onClick={() => signOut()}
            >
              Sign out
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
