"use client";

import { use, useEffect } from "react";

import { AppSidebar } from "@/components/layout/AppSidebar";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { RequireAuth } from "@/lib/auth/RequireAuth";
import { LAST_BRAND_KEY } from "@/lib/constants";

export default function BrandLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  useEffect(() => {
    try {
      localStorage.setItem(LAST_BRAND_KEY, id);
    } catch {
      /* ignore */
    }
  }, [id]);

  // h-screen wrapper: react-resizable-panels sets the group's height to 100%,
  // which needs a parent with a definite height (body is only min-h-full).
  return (
    <RequireAuth>
      <div className="h-screen overflow-hidden">
        <ResizablePanelGroup direction="horizontal" autoSaveId="rankforge:shell">
          <ResizablePanel defaultSize={16} minSize={11} maxSize={26}>
            <AppSidebar brandId={id} />
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel minSize={50}>
            {/* Panels set inline overflow:hidden; scroll must live on an inner box. */}
            <div className="h-full overflow-y-auto">{children}</div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </RequireAuth>
  );
}
