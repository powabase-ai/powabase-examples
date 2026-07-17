"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  briefsApi,
  researchApi,
  sourcesApi,
  templatesApi,
  TERMINAL_RESEARCH,
  type BriefUpdate,
  type ResearchRun,
} from "@/lib/api";

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
    staleTime: Infinity,
  });
}

export function useBrandSources(businessId: string) {
  return useQuery({
    queryKey: ["sources", businessId],
    queryFn: () => sourcesApi.listByBrand(businessId),
    enabled: !!businessId,
  });
}

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
    mutationFn: (vars: {
      topic: string;
      depth: string;
      evaluate_sources?: boolean;
    }) => researchApi.run({ business_id: businessId, ...vars }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["research", businessId] }),
  });
}

export function useDeleteResearchRun(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => researchApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["research", businessId] });
      // Its captured sources go away with it — refresh the sources library too.
      qc.invalidateQueries({ queryKey: ["sources", businessId] });
    },
  });
}

export function useSourceMarkdown(sourceId: string | null) {
  return useQuery({
    queryKey: ["source-markdown", sourceId],
    queryFn: () => sourcesApi.markdown(sourceId as string),
    enabled: !!sourceId,
    staleTime: 5 * 60_000,
  });
}

/** Whether a source has 'original page' renders (uploaded PDFs) + their dimensions. */
export function useSourceMeta(sourceId: string | null) {
  return useQuery({
    queryKey: ["source-meta", sourceId],
    queryFn: () => sourcesApi.meta(sourceId as string),
    enabled: !!sourceId,
    staleTime: 5 * 60_000,
  });
}

/** One rendered page image as a Blob (lazy — `enabled` gated by an IntersectionObserver
 *  in the viewer). The fetch uses `cache: "no-store"`, so React Query — not the browser
 *  HTTP cache — is the cache here; a long staleTime keeps a scrolled-past page from
 *  refetching on the same session. */
export function useSourcePageBlob(
  sourceId: string | null,
  index: number,
  enabled: boolean
) {
  return useQuery({
    queryKey: ["source-page", sourceId, index],
    queryFn: () => sourcesApi.pageImage(sourceId as string, index),
    enabled: enabled && !!sourceId,
    staleTime: 50 * 60_000,
    retry: 1,
  });
}

export function useDeleteBrandSources(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rowIds: string[]) => sourcesApi.bulkDelete(businessId, rowIds),
    // Invalidate on settle (not just success): a partial failure can leave rows deleted
    // server-side while the request errored — refetch either way so the list never keeps
    // showing rows that are already gone.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["sources", businessId] });
      // Deleting sources can empty a run's source list — refresh the runs view too.
      qc.invalidateQueries({ queryKey: ["research", businessId] });
    },
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
    mutationFn: (vars: { researchRunId: string; articleType?: string }) =>
      briefsApi.generate(vars.researchRunId, vars.articleType),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["briefs", businessId] }),
  });
}

export function useUpdateBrief(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; data: BriefUpdate }) =>
      briefsApi.update(vars.id, vars.data),
    // The briefs list (keyed by brand) drives both the per-run brief map and the
    // selected brief on the research page — refresh it so the edit shows immediately.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["briefs", businessId] }),
  });
}
