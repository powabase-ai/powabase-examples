"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { publishApi, type PublishTarget } from "@/lib/api";

export function usePublications(articleId: string) {
  return useQuery({
    queryKey: ["publications", articleId],
    queryFn: () => publishApi.publications(articleId),
    enabled: !!articleId,
  });
}

export function usePublish(articleId: string, brandId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      target_type: PublishTarget;
      config?: Record<string, unknown>;
    }) => publishApi.publish(articleId, body),
    onSuccess: () => {
      // Status flips to "published" server-side — refresh the article +
      // publication history, and the list/board views keyed on the brand
      // (mirrors useUnpublish so status changes don't lag there).
      qc.invalidateQueries({ queryKey: ["article", articleId] });
      qc.invalidateQueries({ queryKey: ["publications", articleId] });
      qc.invalidateQueries({ queryKey: ["articles", brandId] });
      qc.invalidateQueries({ queryKey: ["clusters", brandId] });
    },
  });
}

export function useUnpublish(articleId: string, brandId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => publishApi.unpublish(articleId),
    onSuccess: (data) => {
      // Reverted to draft + detached from its cluster — refresh article, list, clusters.
      qc.setQueryData(["article", articleId], data);
      qc.invalidateQueries({ queryKey: ["publications", articleId] });
      qc.invalidateQueries({ queryKey: ["articles", brandId] });
      qc.invalidateQueries({ queryKey: ["clusters", brandId] });
    },
  });
}
