"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { researchApi, briefsApi } from "@/lib/api";

export function useResearchRuns(businessId: string) {
  return useQuery({
    queryKey: ["research", businessId],
    queryFn: () => researchApi.listByBrand(businessId),
    enabled: !!businessId,
  });
}

export function useRunResearch(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { topic: string; depth: string }) =>
      researchApi.run({ business_id: businessId, ...vars }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["research", businessId] }),
  });
}

export function useBriefs(businessId: string) {
  return useQuery({
    queryKey: ["briefs", businessId],
    queryFn: () => briefsApi.listByBrand(businessId),
    enabled: !!businessId,
  });
}

export function useGenerateBrief(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (researchRunId: string) => briefsApi.generate(researchRunId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["briefs", businessId] }),
  });
}
