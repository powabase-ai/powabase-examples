"use client";

import { use } from "react";

import { AppSidebar } from "@/components/layout/AppSidebar";

export default function BrandLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <div className="flex min-h-screen">
      <AppSidebar brandId={id} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
