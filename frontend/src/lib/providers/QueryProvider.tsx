"use client";

import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
        // Surface fetch failures so a broken list isn't indistinguishable from
        // "no data". Auth 401s are handled (refresh) before reaching here.
        queryCache: new QueryCache({
          onError: (error) => {
            const msg = error instanceof Error ? error.message : "Request failed";
            if (!/\b401\b/.test(msg)) toast.error(msg);
          },
        }),
        mutationCache: new MutationCache({}),
      })
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
