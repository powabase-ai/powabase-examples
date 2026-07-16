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

export function useGenerateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (angle: Angle) => linkedInApi.generate(articleId, angle),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}

export function useUpdateLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ postId, body }: { postId: string; body: string }) =>
      linkedInApi.update(articleId, postId, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}

export function useDeleteLinkedInPost(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postId: string) => linkedInApi.remove(articleId, postId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["linkedin", articleId] }),
  });
}
