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
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteBrand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => brandsApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
