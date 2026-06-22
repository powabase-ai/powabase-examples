"use client";

import { use, useEffect } from "react";

import { AppSidebar } from "@/components/layout/AppSidebar";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
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

  return (
    <ResizablePanelGroup
      direction="horizontal"
      autoSaveId="rankforge:shell"
      className="h-screen"
    >
      <ResizablePanel defaultSize={16} minSize={11} maxSize={26}>
        <AppSidebar brandId={id} />
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel minSize={50} className="overflow-y-auto">
        {children}
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
