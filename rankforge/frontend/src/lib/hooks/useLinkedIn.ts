"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { linkedInApi, type Angle } from "@/lib/api";

export function useLinkedInPosts(articleId: string) {
  return useQuery({
    queryKey: ["linkedin", articleId],
    queryFn: () => linkedInApi.list(articleId),
    enabled: !!articleId,
  });
}

/** All of a brand's posts with their source-article info — drives the Social page. */
export function useBrandLinkedInPosts(businessId: string) {
  return useQuery({
    queryKey: ["linkedin-brand", businessId],
    queryFn: () => linkedInApi.listByBrand(businessId),
    enabled: !!businessId,
  });
}

// Mutations invalidate BOTH views: the per-article list and the brand-wide Social
// list (prefix key — the mutation doesn't know the brand id, and the refetch is cheap).
function invalidateLinkedIn(
  qc: ReturnType<typeof useQueryClient>,
  articleId: string
) {
  qc.invalidateQueries({ queryKey: ["linkedin", articleId] });
  qc.invalidateQueries({ queryKey: ["linkedin-brand"] });
}

export function useGenerateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (angle: Angle) => linkedInApi.generate(articleId, angle),
    onSuccess: () => invalidateLinkedIn(qc, articleId),
  });
}

export function useUpdateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ postId, body }: { postId: string; body: string }) =>
      linkedInApi.update(articleId, postId, body),
    onSuccess: () => invalidateLinkedIn(qc, articleId),
  });
}

export function useDeleteLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postId: string) => linkedInApi.remove(articleId, postId),
    onSuccess: () => invalidateLinkedIn(qc, articleId),
  });
}
