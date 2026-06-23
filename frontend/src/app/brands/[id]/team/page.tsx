"use client";

import { Loader2, Users } from "lucide-react";
import { toast } from "sonner";

import { Card, CardContent } from "@/components/ui/card";
import { useMembers, useSetRole } from "@/lib/hooks/useAccount";
import { useAuth } from "@/lib/auth/AuthProvider";
import type { Role } from "@/lib/api";

const ROLES: { value: Role; blurb: string }[] = [
  { value: "writer", blurb: "Draft & submit for review" },
  { value: "editor", blurb: "Approve & publish" },
  { value: "admin", blurb: "Manage roles" },
];

export default function TeamPage() {
  const { data: members, isLoading } = useMembers();
  const setRole = useSetRole();
  const { profile } = useAuth();
  const isAdmin = profile?.role === "admin";

  function changeRole(id: string, role: Role) {
    setRole.mutate(
      { id, role },
      {
        onSuccess: () => toast.success(`Role updated to ${role}`),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Update failed"),
      }
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <div className="mb-2 flex items-center gap-2">
        <Users className="size-5 text-muted-foreground" />
        <h1 className="font-display text-2xl font-bold">Team</h1>
      </div>
      <p className="mb-6 text-sm text-muted-foreground">
        Everyone shares this workspace. Roles gate the editorial workflow —{" "}
        <span className="font-medium">writers</span> draft and submit,{" "}
        <span className="font-medium">editors</span> approve and publish, and{" "}
        <span className="font-medium">admins</span> manage roles.
        {!isAdmin && " Only admins can change roles."}
      </p>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      <Card>
        <CardContent className="divide-y divide-border p-0">
          {members?.map((m) => (
            <div
              key={m.id}
              className="flex items-center justify-between gap-4 px-5 py-3.5"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">
                  {m.email ?? m.id}
                  {m.id === profile?.id && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      (you)
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {setRole.isPending && setRole.variables?.id === m.id && (
                  <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                )}
                <select
                  value={m.role}
                  disabled={!isAdmin || setRole.isPending}
                  onChange={(e) => changeRole(m.id, e.target.value as Role)}
                  className="h-8 rounded-md border border-input bg-card px-2 text-xs font-medium capitalize text-foreground outline-none focus:ring-1 focus:ring-[rgb(var(--ember))] disabled:opacity-60"
                  aria-label={`Role for ${m.email ?? m.id}`}
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.value}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-1.5 text-xs text-muted-foreground">
        {ROLES.map((r) => (
          <div key={r.value}>
            <span className="font-medium capitalize text-foreground">
              {r.value}
            </span>{" "}
            — {r.blurb}
          </div>
        ))}
      </div>
    </div>
  );
}
