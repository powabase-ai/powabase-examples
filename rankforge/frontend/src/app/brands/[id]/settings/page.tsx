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
  useUploadBrandLogo,
} from "@/lib/hooks/useBrands";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

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

      <BrandLogoCard brand={brand} />

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

/** Brand logo: upload to public storage, or remove. Saves immediately (independent of
 *  the profile form). Shown in the brand switcher. */
function BrandLogoCard({ brand }: { brand?: BusinessProfile }) {
  const upload = useUploadBrandLogo();
  const update = useUpdateBrand();
  if (!brand) return null;

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error("Image must be under 5 MB");
      return;
    }
    upload.mutate(
      { id: brand!.id, file },
      {
        onSuccess: () => toast.success("Logo updated"),
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "Upload failed"),
      }
    );
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="text-base">Logo</CardTitle>
      </CardHeader>
      <CardContent className="flex items-center gap-4">
        {brand.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={brand.logo_url}
            alt="Brand logo"
            className="size-14 shrink-0 rounded-md border border-border bg-white object-contain"
          />
        ) : (
          <div className="flex size-14 shrink-0 items-center justify-center rounded-md border border-dashed border-border text-[10px] text-muted-foreground">
            No logo
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <label className="inline-flex h-8 cursor-pointer items-center rounded-md border border-input px-3 text-xs font-medium hover:bg-secondary">
              {upload.isPending
                ? "Uploading…"
                : brand.logo_url
                  ? "Replace"
                  : "Upload"}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={onPick}
                disabled={upload.isPending}
              />
            </label>
            {brand.logo_url && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  update.mutate(
                    { id: brand.id, data: { logo_url: null } },
                    {
                      onSuccess: () => toast.success("Logo removed"),
                      onError: (err) =>
                        toast.error(
                          err instanceof Error ? err.message : "Remove failed"
                        ),
                    }
                  )
                }
                disabled={update.isPending}
              >
                Remove
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            PNG, JPG, or WebP, up to 5 MB. Shown in the brand switcher.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
