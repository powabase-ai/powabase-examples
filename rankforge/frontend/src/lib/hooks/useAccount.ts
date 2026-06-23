"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { accountApi, type Role } from "@/lib/api";

export function useMembers() {
  return useQuery({ queryKey: ["members"], queryFn: accountApi.members });
}

export function useSetRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) =>
      accountApi.setRole(id, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
}
