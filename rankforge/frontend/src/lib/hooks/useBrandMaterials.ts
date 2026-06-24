"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  materialsApi,
  materialsRunning,
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
    mutationFn: (urls: string[]) => materialsApi.ingest(businessId, urls),
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
