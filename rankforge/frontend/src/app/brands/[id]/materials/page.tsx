"use client";

import { use } from "react";
import { Library } from "lucide-react";

import { BrandMaterials } from "@/components/brand/BrandMaterials";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";

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
      <PageBody className="max-w-2xl">
        <BrandMaterials brandId={id} />
      </PageBody>
    </Page>
  );
}
