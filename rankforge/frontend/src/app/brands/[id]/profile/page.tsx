"use client";

import * as React from "react";
import { KeyRound, Loader2, UserRound } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useAuth } from "@/lib/auth/AuthProvider";

const MIN_PASSWORD = 6;

export default function ProfilePage() {
  const { profile, changePassword } = useAuth();

  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (next.length < MIN_PASSWORD) {
      toast.error(`New password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (next !== confirm) {
      toast.error("New passwords don't match.");
      return;
    }
    if (next === current) {
      toast.error("New password must be different from the current one.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      toast.success("Password updated.");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't update password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page>
      <PageHeader icon={UserRound} title="Profile" />
      <PageBody className="max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Account</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <div className="flex items-center justify-between gap-4">
              <span className="text-muted-foreground">Email</span>
              <span className="font-medium">{profile?.email ?? "—"}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
              <span className="text-muted-foreground">Role</span>
              <span className="font-medium capitalize">{profile?.role ?? "—"}</span>
            </div>
          </CardContent>
        </Card>

        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="size-4" /> Change password
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="grid gap-4">
              <div className="grid gap-1.5">
                <Label htmlFor="current_password">Current password</Label>
                <Input
                  id="current_password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  placeholder="••••••••"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="new_password">New password</Label>
                <Input
                  id="new_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD}
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  placeholder="At least 6 characters"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="confirm_password">Confirm new password</Label>
                <Input
                  id="confirm_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="Re-type the new password"
                />
              </div>
              <div className="flex justify-end">
                <Button
                  type="submit"
                  variant="gold"
                  disabled={busy || !current || !next || !confirm}
                >
                  {busy && <Loader2 className="animate-spin" />}
                  Update password
                </Button>
              </div>
            </form>
            <p className="mt-3 text-xs text-muted-foreground">
              You&apos;ll need your current password to set a new one. This changes your
              sign-in password everywhere.
            </p>
          </CardContent>
        </Card>
      </PageBody>
    </Page>
  );
}
