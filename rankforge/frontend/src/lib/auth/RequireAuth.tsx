"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { useAuth } from "./AuthProvider";
import { InviteGate } from "./InviteGate";

/** Gate authenticated pages — redirect to /login when there is no session, and hold
 *  registered-but-unverified accounts on the invite-code screen until they redeem it. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { session, profile, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !session) router.replace("/login");
  }, [loading, session, router]);

  if (loading || !session) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );
  }
  // Signed in but hasn't cleared the signup invite gate → show the code screen only.
  // Only block on a POSITIVELY-unverified profile: if the profile fetch transiently
  // failed (profile null), fall through — the backend still 403s every gated route, so
  // failing open here can't leak data, and it avoids trapping a verified user on a
  // spinner. The gate re-asserts the moment /api/me succeeds.
  if (profile && !profile.invite_verified) return <InviteGate />;
  return <>{children}</>;
}
