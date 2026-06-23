"use client";

import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
        // Surface fetch failures so a broken list isn't indistinguishable from
        // "no data". 401s are handled (refresh) before reaching here, so skip them.
        queryCache: new QueryCache({
          onError: (error) => {
            if (error instanceof ApiError && error.status === 401) return;
            toast.error(error instanceof Error ? error.message : "Request failed");
          },
        }),
      })
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
