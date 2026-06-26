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

export function usePublish(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      target_type: PublishTarget;
      config?: Record<string, unknown>;
    }) => publishApi.publish(articleId, body),
    onSuccess: () => {
      // Status flips to "published" — refresh the article + publication history.
      qc.invalidateQueries({ queryKey: ["article", articleId] });
      qc.invalidateQueries({ queryKey: ["publications", articleId] });
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
      qc.invalidateQueries({ queryKey: ["articles", brandId] });
      qc.invalidateQueries({ queryKey: ["clusters", brandId] });
    },
  });
}
