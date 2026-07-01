"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  brandsApi,
  type BusinessProfile,
  type BusinessProfileInput,
} from "@/lib/api";

const KEY = ["business-profiles"];

export function useBrands() {
  return useQuery<BusinessProfile[]>({ queryKey: KEY, queryFn: brandsApi.list });
}

export function useBrand(id: string) {
  return useQuery<BusinessProfile>({
    queryKey: ["business-profile", id],
    queryFn: () => brandsApi.get(id),
    enabled: !!id,
  });
}

export function useCreateBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: BusinessProfileInput) => brandsApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Partial<BusinessProfileInput>;
    }) => brandsApi.update(id, data),
    // Refresh BOTH the list and the single-brand detail cache — else a url_pattern
    // save renders stale in InternalLinksPanel/PublishDialog until staleTime elapses.
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["business-profile", id] });
    },
  });
}

export function useUploadBrandLogo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      brandsApi.uploadLogo(id, file),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["business-profile", id] });
    },
  });
}

export function useDeleteBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => brandsApi.remove(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.removeQueries({ queryKey: ["business-profile", id] });
    },
  });
}
