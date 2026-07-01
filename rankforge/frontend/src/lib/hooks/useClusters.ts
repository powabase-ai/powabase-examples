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

export function useCreateCluster(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { label: string; theme?: string }) =>
      clustersApi.create(businessId, data),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["clusters", businessId] }),
  });
}

export function useMoveArticle(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      toClusterId,
      articleId,
    }: {
      toClusterId: string;
      articleId: string;
    }) => clustersApi.moveArticle(toClusterId, articleId),
    // The article left one cluster and joined another — both detail views and the list
    // (member counts, pillar rows) are now stale. The source cluster id isn't returned,
    // so refresh the whole set rather than surgically patch two caches.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["clusters", businessId] });
      qc.invalidateQueries({ queryKey: ["cluster"] });
    },
  });
}

export function useAnalyzeGaps() {
  return useMutation({
    mutationFn: (clusterId: string) => clustersApi.analyzeGaps(clusterId),
  });
}

export function useDeleteCluster(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (clusterId: string) => clustersApi.remove(clusterId),
    onSuccess: (_d, clusterId) => {
      qc.invalidateQueries({ queryKey: ["clusters", businessId] });
      qc.removeQueries({ queryKey: ["cluster", clusterId] });
      // Members became unclustered — their article rows and any opportunity tags
      // tied to this cluster changed.
      qc.invalidateQueries({ queryKey: ["articles", businessId] });
      qc.invalidateQueries({ queryKey: ["opportunities", businessId] });
    },
  });
}

export function useBackfillClusters(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => clustersApi.backfill(businessId),
    // Backfill runs inline and returns its count; the new clusters are ready now.
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["clusters", businessId] }),
  });
}
