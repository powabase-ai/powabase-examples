"use client";

import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/AuthProvider";
import { useAcceptInvite } from "@/lib/hooks/useOrg";
import { PENDING_INVITE_KEY } from "@/lib/constants";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <span className="font-display text-3xl font-bold tracking-tight">
            Rank<span className="text-[rgb(var(--ember))]">Forge</span>
          </span>
        </div>
        <div className="rounded-xl border border-border bg-card p-6 shadow-sm">
          {children}
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex min-h-screen items-center justify-center text-muted-foreground">
      <Loader2 className="size-5 animate-spin" />
    </div>
  );
}

function AcceptInviteInner() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const router = useRouter();
  const qc = useQueryClient();
  const { session, profile, loading, refreshProfile } = useAuth();
  const accept = useAcceptInvite();

  // Stash the token so it survives the sign-in / sign-up round-trip (the login page
  // resumes it after auth).
  React.useEffect(() => {
    if (token) localStorage.setItem(PENDING_INVITE_KEY, token);
  }, [token]);

  function onAccept() {
    accept.mutate(token, {
      onSuccess: async (org) => {
        localStorage.removeItem(PENDING_INVITE_KEY);
        // Org changed → the caller's role + every cached tenant list are now stale.
        qc.clear();
        await refreshProfile();
        toast.success(`You've joined ${org.name}.`);
        router.replace("/");
      },
      onError: (e) =>
        toast.error(
          e instanceof Error
            ? e.message
            : "This invite is invalid or has already been used."
        ),
    });
  }

  if (loading) return <Spinner />;

  if (!token) {
    return (
      <Shell>
        <h1 className="mb-2 font-display text-lg font-semibold">Invalid invite</h1>
        <p className="text-sm text-muted-foreground">
          This invite link is missing its token. Ask your admin to send you a fresh
          link.
        </p>
      </Shell>
    );
  }

  if (!session) {
    return (
      <Shell>
        <h1 className="mb-2 font-display text-lg font-semibold">
          Join the workspace
        </h1>
        <p className="mb-5 text-sm text-muted-foreground">
          You&apos;ve been invited to a RankForge workspace. Sign in or create an
          account to accept — we&apos;ll bring you right back here.
        </p>
        <Button asChild variant="gold" className="w-full">
          <Link href="/login">Sign in or create an account</Link>
        </Button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="mb-2 font-display text-lg font-semibold">Accept invitation</h1>
      <p className="mb-5 text-sm text-muted-foreground">
        {profile?.email ? (
          <>
            Signed in as <span className="font-medium">{profile.email}</span>. Accept
            to join the workspace you were invited to. This replaces your current
            workspace.
          </>
        ) : (
          "Accept to join the workspace you were invited to."
        )}
      </p>
      <Button
        variant="gold"
        className="w-full"
        onClick={onAccept}
        disabled={accept.isPending}
      >
        {accept.isPending && <Loader2 className="animate-spin" />}
        Accept invitation
      </Button>
    </Shell>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<Spinner />}>
      <AcceptInviteInner />
    </Suspense>
  );
}
