"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  materialsApi,
  materialsRunning,
  type MaterialsIngestRequest,
  type MaterialsView,
} from "@/lib/api";

export function useBrandMaterials(businessId: string) {
  return useQuery({
    queryKey: ["materials", businessId],
    queryFn: () => materialsApi.get(businessId),
    enabled: !!businessId,
    // Poll every 3s while an ingest is running (phase not terminal / not empty).
    refetchInterval: (query) => {
      const view = query.state.data as MaterialsView | undefined;
      return materialsRunning(view?.progress) ? 3000 : false;
    },
  });
}

export function useIngestMaterials(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MaterialsIngestRequest) =>
      materialsApi.ingest(businessId, body),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["materials", businessId] }),
  });
}

export function useUploadMaterialFile(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => materialsApi.uploadFile(businessId, file),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["materials", businessId] }),
  });
}

export function useRemoveMaterial(businessId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rowId: string) => materialsApi.remove(businessId, rowId),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["materials", businessId] }),
  });
}

/** Fetch one source's extracted markdown on demand (for the inspect modal).
 *  Only runs when a row is being inspected (rowId non-null) and is cached so
 *  re-opening the same source doesn't refetch. */
export function useMaterialContent(businessId: string, rowId: string | null) {
  return useQuery({
    queryKey: ["material-content", businessId, rowId],
    queryFn: () => materialsApi.content(businessId, rowId as string),
    enabled: !!businessId && !!rowId,
    staleTime: 5 * 60 * 1000,
  });
}
