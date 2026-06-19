"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { BrandForm } from "@/components/BrandForm";
import { useBrands, useCreateBrand } from "@/lib/hooks/useBrands";
import { LAST_BRAND_KEY } from "@/lib/constants";
import type { BusinessProfileInput } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const { data: brands, isLoading } = useBrands();
  const createBrand = useCreateBrand();
  const [open, setOpen] = React.useState(false);

  // Go straight to a workspace: last opened (if still present) else the first brand.
  React.useEffect(() => {
    if (!brands || brands.length === 0) return;
    const last =
      typeof window !== "undefined" ? localStorage.getItem(LAST_BRAND_KEY) : null;
    const target = brands.find((b) => b.id === last) ?? brands[0];
    router.replace(`/brands/${target.id}`);
  }, [brands, router]);

  async function handleCreate(data: BusinessProfileInput) {
    try {
      const b = await createBrand.mutateAsync(data);
      setOpen(false);
      router.replace(`/brands/${b.id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    }
  }

  // Loading, or redirecting to a workspace.
  if (isLoading || (brands && brands.length > 0)) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );
  }

  // First-run: no brands yet.
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <span className="font-display text-2xl font-bold tracking-tight">
        Rank<span className="text-[rgb(var(--accent-gold))]">Forge</span>
      </span>
      <p className="max-w-sm text-sm text-muted-foreground">
        Create your first brand to start researching and drafting SEO/GEO content.
      </p>
      <Button variant="gold" onClick={() => setOpen(true)}>
        <Plus /> New brand
      </Button>
      <BrandForm
        open={open}
        onOpenChange={setOpen}
        brand={null}
        onSave={handleCreate}
        saving={createBrand.isPending}
      />
    </div>
  );
}
