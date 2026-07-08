"use client";

import * as React from "react";
import { Copy, Loader2, Trash2, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useMembers, useSetRole } from "@/lib/hooks/useAccount";
import { useCreateInvite, useInvites, useRevokeInvite } from "@/lib/hooks/useOrg";
import { useAuth } from "@/lib/auth/AuthProvider";
import type { Role } from "@/lib/api";

const ROLES: { value: Role; blurb: string }[] = [
  { value: "writer", blurb: "Draft & submit for review" },
  { value: "editor", blurb: "Approve & publish" },
  { value: "admin", blurb: "Manage roles" },
];

/** The out-of-band accept link an admin shares with an invitee. */
function inviteLink(token: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}/accept-invite?token=${encodeURIComponent(token)}`;
}

async function copyInviteLink(token: string, msg = "Invite link copied") {
  try {
    await navigator.clipboard.writeText(inviteLink(token));
    toast.success(msg);
  } catch {
    toast.error("Couldn't copy the link — copy it from the address manually");
  }
}

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
    <Page>
      <PageHeader icon={Users} title="Team" />
      <PageBody>
      <p className="mb-6 text-sm text-muted-foreground">
        Everyone shares this workspace. Roles gate the editorial workflow —{" "}
        <span className="font-medium">writers</span> draft and submit,{" "}
        <span className="font-medium">editors</span> approve and publish, and{" "}
        <span className="font-medium">admins</span> manage roles.
        {!isAdmin && " Only admins can invite teammates or change roles."}
      </p>

      {isAdmin && <InviteTeammates />}

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
      </PageBody>
    </Page>
  );
}

/** Admin-only. Create/list/revoke teammate invites. Mounted only for admins so the
 *  admin-gated /api/org/invites list never fires for non-admins. */
function InviteTeammates() {
  const { data: invites } = useInvites();
  const create = useCreateInvite();
  const revoke = useRevokeInvite();
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("writer");

  const pending = (invites ?? []).filter((i) => !i.accepted_at);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value) return;
    create.mutate(
      { email: value, role },
      {
        onSuccess: (inv) => {
          setEmail("");
          if (inv.token) {
            copyInviteLink(
              inv.token,
              `Invite created for ${inv.email} — link copied. Share it with them.`
            );
          }
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : "Could not create invite"
          ),
      }
    );
  }

  function doRevoke(id: string) {
    revoke.mutate(id, {
      onSuccess: () => toast.success("Invite revoked"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Revoke failed"),
    });
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle className="text-base">Invite a teammate</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <div className="grid min-w-[220px] flex-1 gap-1.5">
            <label htmlFor="invite_email" className="text-xs text-muted-foreground">
              Email
            </label>
            <Input
              id="invite_email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@company.com"
            />
          </div>
          <div className="grid gap-1.5">
            <label htmlFor="invite_role" className="text-xs text-muted-foreground">
              Role
            </label>
            <select
              id="invite_role"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              className="h-9 rounded-md border border-input bg-card px-2 text-sm font-medium capitalize text-foreground outline-none focus:ring-1 focus:ring-[rgb(var(--ember))]"
            >
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.value}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" variant="gold" disabled={create.isPending || !email.trim()}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <UserPlus />}
            Create invite
          </Button>
        </form>

        <p className="text-xs text-muted-foreground">
          Creating an invite gives you a private link to share directly with your
          teammate — no email is sent. They sign up (or sign in), open the link, and
          join this workspace with the role you picked.
        </p>

        {pending.length > 0 && (
          <div className="divide-y divide-border rounded-md border border-border">
            {pending.map((i) => (
              <div
                key={i.id}
                className="flex items-center justify-between gap-3 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{i.email}</div>
                  <div className="text-xs capitalize text-muted-foreground">
                    {i.role} · pending
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {i.token && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => copyInviteLink(i.token!)}
                    >
                      <Copy /> Copy link
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => doRevoke(i.id)}
                    disabled={revoke.isPending}
                    aria-label={`Revoke invite for ${i.email}`}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
