"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { linkedInApi, type Angle } from "@/lib/api";

/** All of a brand's posts with their source-article info — drives the Social page.
 *  This brand-wide query is the only subscribed LinkedIn view, so the mutations below
 *  invalidate just its prefix key (the per-article endpoint has no live consumer). */
export function useBrandLinkedInPosts(businessId: string) {
  return useQuery({
    queryKey: ["linkedin-brand", businessId],
    queryFn: () => linkedInApi.listByBrand(businessId),
    enabled: !!businessId,
  });
}

// Prefix key — the mutation doesn't know the brand id, and the refetch is cheap.
function invalidateBrandLinkedIn(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["linkedin-brand"] });
}

export function useGenerateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (angle: Angle) => linkedInApi.generate(articleId, angle),
    onSuccess: () => invalidateBrandLinkedIn(qc),
  });
}

export function useUpdateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ postId, body }: { postId: string; body: string }) =>
      linkedInApi.update(articleId, postId, body),
    onSuccess: () => invalidateBrandLinkedIn(qc),
  });
}

export function useDeleteLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postId: string) => linkedInApi.remove(articleId, postId),
    onSuccess: () => invalidateBrandLinkedIn(qc),
  });
}
