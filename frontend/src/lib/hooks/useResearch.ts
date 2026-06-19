"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  briefsApi,
  researchApi,
  TERMINAL_RESEARCH,
  type ResearchRun,
} from "@/lib/api";

export function useResearchRuns(businessId: string) {
  return useQuery({
    queryKey: ["research", businessId],
    queryFn: () => researchApi.listByBrand(businessId),
    enabled: !!businessId,
    // Poll while any run is still in progress (searching/scraping/…).
    refetchInterval: (query) => {
      const runs = query.state.data as ResearchRun[] | undefined;
      const inProgress = runs?.some((r) => !TERMINAL_RESEARCH.includes(r.status));
      return inProgress ? 2500 : false;
    },
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

export function useSourceMarkdown(sourceId: string | null) {
  return useQuery({
    queryKey: ["source-markdown", sourceId],
    queryFn: () => researchApi.sourceMarkdown(sourceId as string),
    enabled: !!sourceId,
    staleTime: 5 * 60_000,
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
