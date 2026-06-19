"use client";

import { use, useEffect } from "react";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { LAST_BRAND_KEY } from "@/lib/constants";

export default function BrandLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  // Remember the last-opened brand so "/" can jump straight back here.
  useEffect(() => {
    try {
      localStorage.setItem(LAST_BRAND_KEY, id);
    } catch {
      /* ignore */
    }
  }, [id]);

  return (
    <div className="flex min-h-screen">
      <AppSidebar brandId={id} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
