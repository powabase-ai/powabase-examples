"use client";

import * as React from "react";
import { use } from "react";
import { useRouter } from "next/navigation";
import { Plus, Settings as SettingsIcon, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BrandForm } from "@/components/BrandForm";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import {
  BrandFields,
  brandToForm,
  emptyBrandForm,
  formToPayload,
  type BrandFormState,
} from "@/components/brand/BrandFields";
import {
  useBrands,
  useCreateBrand,
  useDeleteBrand,
  useUpdateBrand,
} from "@/lib/hooks/useBrands";
import type { BusinessProfileInput } from "@/lib/api";

export default function BrandSettings({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { data: brands } = useBrands();
  const brand = brands?.find((b) => b.id === id);
  const updateBrand = useUpdateBrand();
  const deleteBrand = useDeleteBrand();
  const createBrand = useCreateBrand();

  const [form, setForm] = React.useState<BrandFormState>(emptyBrandForm());
  const [newOpen, setNewOpen] = React.useState(false);

  React.useEffect(() => {
    if (brand) setForm(brandToForm(brand));
  }, [brand]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await updateBrand.mutateAsync({ id, data: formToPayload(form) });
      toast.success("Saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function remove() {
    if (
      !confirm(
        `Delete "${brand?.name}"? This removes its research, briefs, and articles.`
      )
    )
      return;
    try {
      await deleteBrand.mutateAsync(id);
      const remaining = brands?.filter((b) => b.id !== id) ?? [];
      router.replace(remaining[0] ? `/brands/${remaining[0].id}` : "/");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function createNew(data: BusinessProfileInput) {
    try {
      const b = await createBrand.mutateAsync(data);
      setNewOpen(false);
      router.replace(`/brands/${b.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Create failed");
    }
  }

  return (
    <Page>
      <PageHeader
        icon={SettingsIcon}
        title="Settings"
        actions={
          <Button variant="outline" size="sm" onClick={() => setNewOpen(true)}>
            <Plus /> New brand
          </Button>
        }
      />
      <PageBody className="max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Brand profile</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={save} className="grid gap-4">
            <BrandFields value={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} />
            <div className="flex justify-end">
              <Button type="submit" variant="gold" disabled={updateBrand.isPending || !form.name.trim()}>
                {updateBrand.isPending ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card className="mt-6 border-[rgb(var(--destructive))]/30">
        <CardHeader>
          <CardTitle className="text-base text-destructive">Danger zone</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Delete this brand and all its research, briefs, and articles.
          </p>
          <Button variant="destructive" onClick={remove} disabled={deleteBrand.isPending}>
            <Trash2 /> Delete brand
          </Button>
        </CardContent>
      </Card>

      <BrandForm
        open={newOpen}
        onOpenChange={setNewOpen}
        brand={null}
        onSave={createNew}
        saving={createBrand.isPending}
      />
      </PageBody>
    </Page>
  );
}
