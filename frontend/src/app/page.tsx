"use client";

import * as React from "react";
import { Globe, Pencil, Plus, Target, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BrandForm } from "@/components/BrandForm";
import {
  useBrands,
  useCreateBrand,
  useDeleteBrand,
  useUpdateBrand,
} from "@/lib/hooks/useBrands";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

export default function Home() {
  const { data: brands, isLoading, error } = useBrands();
  const createBrand = useCreateBrand();
  const updateBrand = useUpdateBrand();
  const deleteBrand = useDeleteBrand();

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<BusinessProfile | null>(null);

  function openNew() {
    setEditing(null);
    setOpen(true);
  }
  function openEdit(b: BusinessProfile) {
    setEditing(b);
    setOpen(true);
  }

  async function handleSave(data: BusinessProfileInput) {
    try {
      if (editing) {
        await updateBrand.mutateAsync({ id: editing.id, data });
        toast.success("Brand updated");
      } else {
        await createBrand.mutateAsync(data);
        toast.success("Brand created");
      }
      setOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function handleDelete(b: BusinessProfile) {
    if (!confirm(`Delete "${b.name}"? This removes its research, briefs, and articles.`))
      return;
    try {
      await deleteBrand.mutateAsync(b.id);
      toast.success("Brand deleted");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2">
            <span className="font-display text-xl font-bold tracking-tight">
              Rank<span className="text-[rgb(var(--accent-gold))]">Forge</span>
            </span>
            <span className="rounded-md bg-secondary px-2 py-0.5 text-xs text-muted-foreground">
              Brands
            </span>
          </div>
          <Button variant="gold" onClick={openNew}>
            <Plus /> New brand
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6">
          <h1 className="font-display text-2xl font-bold">Brand profiles</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Each brand scopes its own research, briefs, articles, and content scouts.
          </p>
        </div>

        {isLoading && <p className="text-sm text-muted-foreground">Loading brands…</p>}
        {error && (
          <p className="text-sm text-destructive">
            Couldn&apos;t reach the backend: {(error as Error).message}
          </p>
        )}

        {brands && brands.length === 0 && (
          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
              <p className="text-sm text-muted-foreground">
                No brands yet. Create your first brand profile to start.
              </p>
              <Button variant="gold" onClick={openNew}>
                <Plus /> New brand
              </Button>
            </CardContent>
          </Card>
        )}

        {brands && brands.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {brands.map((b) => (
              <Card key={b.id} className="flex flex-col">
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="font-display text-lg">{b.name}</CardTitle>
                    <div className="flex gap-1">
                      <Button size="icon" variant="ghost" onClick={() => openEdit(b)} aria-label="Edit">
                        <Pencil />
                      </Button>
                      <Button size="icon" variant="ghost" onClick={() => handleDelete(b)} aria-label="Delete">
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                  {b.niche && <p className="text-sm text-muted-foreground">{b.niche}</p>}
                </CardHeader>
                <CardContent className="mt-auto flex flex-col gap-2 text-xs text-muted-foreground">
                  {b.domain && (
                    <span className="inline-flex items-center gap-1.5">
                      <Globe className="size-3.5" /> {b.domain}
                    </span>
                  )}
                  <span className="inline-flex items-center gap-1.5">
                    <Target className="size-3.5" /> {b.target_keywords.length} keywords ·{" "}
                    {b.seed_topics.length} topics
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Users className="size-3.5" /> {b.competitors.length} competitors
                  </span>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </main>

      <BrandForm
        open={open}
        onOpenChange={setOpen}
        brand={editing}
        onSave={handleSave}
        saving={createBrand.isPending || updateBrand.isPending}
      />
    </div>
  );
}
