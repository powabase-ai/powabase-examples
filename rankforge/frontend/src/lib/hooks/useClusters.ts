"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { clustersApi } from "@/lib/api";

export function useClusters(businessId: string) {
  return useQuery({
    queryKey: ["clusters", businessId],
    queryFn: () => clustersApi.list(businessId),
    enabled: !!businessId,
  });
}

export function useCluster(clusterId: string | null) {
  return useQuery({
    queryKey: ["cluster", clusterId],
    queryFn: () => clustersApi.get(clusterId as string),
    enabled: !!clusterId,
  });
}

export function useSetPillar(clusterId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (articleId: string) => clustersApi.setPillar(clusterId, articleId),
    onSuccess: (data) => {
      qc.setQueryData(["cluster", clusterId], data);
      qc.invalidateQueries({ queryKey: ["clusters", data.business_id] });
    },
  });
}

export function useAnalyzeGaps() {
  return useMutation({
    mutationFn: (clusterId: string) => clustersApi.analyzeGaps(clusterId),
  });
}

export function useBackfillClusters(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clustersApi.backfill(businessId),
    // Backfill is async on the server; refetch the list shortly after.
    onSuccess: () =>
      setTimeout(
        () => qc.invalidateQueries({ queryKey: ["clusters", businessId] }),
        5000
      ),
  });
}
