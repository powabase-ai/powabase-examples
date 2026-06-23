"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { articlesApi } from "@/lib/api";

export function useComments(articleId: string) {
  return useQuery({
    queryKey: ["comments", articleId],
    queryFn: () => articlesApi.comments(articleId),
    enabled: !!articleId,
  });
}

export function useAddComment(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { body: string; anchor?: string | null }) =>
      articlesApi.addComment(articleId, vars.body, vars.anchor),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comments", articleId] }),
  });
}

export function useUpdateComment(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      id: string;
      data: { body?: string; resolved?: boolean };
    }) => articlesApi.updateComment(articleId, vars.id, vars.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comments", articleId] }),
  });
}

export function useRemoveComment(articleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => articlesApi.removeComment(articleId, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["comments", articleId] }),
  });
}
