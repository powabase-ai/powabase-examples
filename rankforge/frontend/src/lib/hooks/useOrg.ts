"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { orgApi, type Role } from "@/lib/api";

/** The caller's organization. */
export function useOrg() {
  return useQuery({ queryKey: ["org"], queryFn: orgApi.get });
}

/** Pending teammate invites. Hits the admin-only /api/org/invites endpoint, so only
 *  the component that calls it should be rendered for admins — the Team page mounts
 *  <InviteTeammates/> behind `isAdmin`, so a non-admin never fires this request. */
export function useInvites() {
  return useQuery({ queryKey: ["org-invites"], queryFn: orgApi.listInvites });
}

export function useCreateInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: Role }) =>
      orgApi.createInvite(email, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-invites"] }),
  });
}

export function useRevokeInvite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => orgApi.revokeInvite(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-invites"] }),
  });
}

/** Accept an invite by its token; the caller joins the inviting org and leaves
 *  their own solo org. Returns the joined organization. */
export function useAcceptInvite() {
  return useMutation({ mutationFn: (token: string) => orgApi.acceptInvite(token) });
}
