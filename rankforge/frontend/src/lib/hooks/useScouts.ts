"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  opportunitiesApi,
  relinkApi,
  scoutsApi,
  type Opportunity,
  type RelinkConfig,
  type ScoutConfig,
  type ScoutRun,
} from "@/lib/api";

// --- re-linking schedule (M6 / Phase 12.3) ---
export function useRelinkConfig(businessId: string) {
  return useQuery({
    queryKey: ["relinkConfig", businessId],
    queryFn: () => relinkApi.get(businessId),
    enabled: !!businessId,
  });
}

export function useUpdateRelink(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Pick<RelinkConfig, "enabled" | "cadence">>) =>
      relinkApi.update(businessId, data),
    onSuccess: (data) => qc.setQueryData(["relinkConfig", businessId], data),
  });
}

export function useRunRelink(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => relinkApi.run(businessId),
    // Results land async; refetch the config shortly so last_found updates.
    onSuccess: () =>
      setTimeout(
        () => qc.invalidateQueries({ queryKey: ["relinkConfig", businessId] }),
        4000
      ),
  });
}

export function useScoutConfig(businessId: string) {
  return useQuery({
    queryKey: ["scoutConfig", businessId],
    queryFn: () => scoutsApi.config(businessId),
    enabled: !!businessId,
  });
}

export function useUpdateScoutConfig(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ScoutConfig>) =>
      scoutsApi.updateConfig(businessId, data),
    onSuccess: (data) => qc.setQueryData(["scoutConfig", businessId], data),
  });
}

export function useScoutRuns(businessId: string) {
  return useQuery({
    queryKey: ["scoutRuns", businessId],
    queryFn: () => scoutsApi.runs(businessId),
    enabled: !!businessId,
    refetchInterval: (query) => {
      const rows = query.state.data as ScoutRun[] | undefined;
      return rows?.some((r) => r.status === "running") ? 3000 : false;
    },
  });
}

export function useRunScout(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => scoutsApi.run(businessId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scoutRuns", businessId] });
      qc.invalidateQueries({ queryKey: ["opportunities", businessId] });
    },
  });
}

export function useOpportunities(businessId: string) {
  return useQuery({
    queryKey: ["opportunities", businessId],
    queryFn: () => opportunitiesApi.list(businessId),
    enabled: !!businessId,
    refetchInterval: (query) => {
      const rows = query.state.data as Opportunity[] | undefined;
      return rows?.some((o) => o.status === "queued" || o.status === "drafting")
        ? 3000
        : false;
    },
  });
}

export function useDraftOpportunity(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => opportunitiesApi.draft(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities", businessId] });
      qc.invalidateQueries({ queryKey: ["articles", businessId] });
    },
  });
}

export function useDismissOpportunity(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => opportunitiesApi.dismiss(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["opportunities", businessId] }),
  });
}

export function useRestoreOpportunity(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => opportunitiesApi.restore(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["opportunities", businessId] }),
  });
}
