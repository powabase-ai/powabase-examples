"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  opportunitiesApi,
  relinkApi,
  scoutsApi,
  type Opportunity,
  type RelinkConfig,
  type ScoutConfig,
  type ScoutPlan,
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

// --- two-phase manual run: plan → review/edit → execute ---
export function usePlanScout(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => scoutsApi.plan(businessId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scoutRuns", businessId] }),
  });
}

export function useScoutRun(runId: string | null) {
  return useQuery({
    queryKey: ["scoutRun", runId],
    queryFn: () => scoutsApi.getRun(runId as string),
    enabled: !!runId,
    refetchInterval: (query) => {
      const r = query.state.data as ScoutRun | undefined;
      if (!r) return 1500;
      if (r.status === "planned" && !r.plan) return 1500; // plan still generating
      if (r.status === "running") return 2500;
      return false;
    },
  });
}

export function useUpdatePlan(runId: string) {
  return useMutation({
    mutationFn: (plan: ScoutPlan) => scoutsApi.updatePlan(runId, plan),
  });
}

export function useExecuteRun(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => scoutsApi.execute(runId),
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

export function useDeleteOpportunity(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => opportunitiesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities", businessId] });
      // A deleted opportunity may have contributed to a cluster's view.
      qc.invalidateQueries({ queryKey: ["clusters", businessId] });
    },
  });
}
