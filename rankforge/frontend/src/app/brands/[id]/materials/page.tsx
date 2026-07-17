"use client";

import { use } from "react";
import { Library } from "lucide-react";

import { BrandMaterials } from "@/components/brand/BrandMaterials";
import { Page, PageHeader } from "@/components/layout/PageHeader";

export default function BrandMaterialsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <Page>
      <PageHeader
        icon={Library}
        title="Materials"
        meta="Your own pages drafts can describe and link to"
      />
      {/* BrandMaterials is a full two-pane (rail + content) that fills the region. */}
      <BrandMaterials brandId={id} />
    </Page>
  );
}
