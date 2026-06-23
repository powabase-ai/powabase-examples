"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  articlesApi,
  TERMINAL_GENERATION,
  type Article,
  type ArticleSummary,
  type ArticleUpdate,
} from "@/lib/api";

export function useArticles(businessId: string) {
  return useQuery({
    queryKey: ["articles", businessId],
    queryFn: () => articlesApi.listByBrand(businessId),
    enabled: !!businessId,
    refetchInterval: (query) => {
      const rows = query.state.data as ArticleSummary[] | undefined;
      const busy = rows?.some(
        (a) => !TERMINAL_GENERATION.includes(a.generation_status)
      );
      return busy ? 2500 : false;
    },
  });
}

export function useArticle(id: string | null) {
  return useQuery({
    queryKey: ["article", id],
    queryFn: () => articlesApi.get(id as string),
    enabled: !!id,
    refetchInterval: (query) => {
      const a = query.state.data as Article | undefined;
      return a && !TERMINAL_GENERATION.includes(a.generation_status) ? 2000 : false;
    },
  });
}

export function useGenerateArticle(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (briefId: string) => articlesApi.generate(briefId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["articles", businessId] }),
  });
}

export function useScoreArticle(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => articlesApi.score(id),
    onSuccess: (data) => qc.setQueryData(["article", id], data),
  });
}

export function useUpdateArticle(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ArticleUpdate) => articlesApi.update(id, data),
    onSuccess: (data) => {
      qc.setQueryData(["article", id], data);
      // A content edit snapshots a new version server-side — keep History fresh.
      qc.invalidateQueries({ queryKey: ["versions", id] });
    },
  });
}

export function useOptimizeArticle(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => articlesApi.optimize(id),
    onSuccess: (data) => qc.setQueryData(["article", id], data),
  });
}

export function useRefineArticle(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => articlesApi.refine(id),
    onSuccess: (data) => qc.setQueryData(["article", id], data),
  });
}

export function useArticleVersions(id: string) {
  return useQuery({
    queryKey: ["versions", id],
    queryFn: () => articlesApi.versions(id),
    enabled: !!id,
  });
}

export function useRestoreVersion(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) => articlesApi.restoreVersion(id, versionId),
    onSuccess: (data) => {
      qc.setQueryData(["article", id], data);
      qc.invalidateQueries({ queryKey: ["versions", id] });
    },
  });
}
