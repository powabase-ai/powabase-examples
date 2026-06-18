"use client";

import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

const csv = (xs: string[] | undefined) => (xs ?? []).join(", ");
const fromCsv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

export function BrandForm({
  open,
  onOpenChange,
  brand,
  onSave,
  saving,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  brand: BusinessProfile | null;
  onSave: (data: BusinessProfileInput) => Promise<void> | void;
  saving?: boolean;
}) {
  const [form, setForm] = React.useState({
    name: "",
    domain: "",
    niche: "",
    audience: "",
    description: "",
    seed_topics: "",
    target_keywords: "",
    competitors: "",
    sitemap_url: "",
  });

  React.useEffect(() => {
    if (!open) return;
    setForm({
      name: brand?.name ?? "",
      domain: brand?.domain ?? "",
      niche: brand?.niche ?? "",
      audience: brand?.audience ?? "",
      description: brand?.description ?? "",
      seed_topics: csv(brand?.seed_topics),
      target_keywords: csv(brand?.target_keywords),
      competitors: csv(brand?.competitors?.map((c) => c.domain)),
      sitemap_url: brand?.sitemap_url ?? "",
    });
  }, [open, brand]);

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await onSave({
      name: form.name.trim(),
      domain: form.domain.trim() || null,
      niche: form.niche.trim() || null,
      audience: form.audience.trim() || null,
      description: form.description.trim() || null,
      seed_topics: fromCsv(form.seed_topics),
      target_keywords: fromCsv(form.target_keywords),
      competitors: fromCsv(form.competitors).map((domain) => ({ domain })),
      sitemap_url: form.sitemap_url.trim() || null,
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">
            {brand ? "Edit brand" : "New brand"}
          </DialogTitle>
          <DialogDescription>
            A brand profile scopes research, briefs, articles, and scouts to one
            business niche.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="name">Name *</Label>
            <Input id="name" required value={form.name} onChange={set("name")} placeholder="Acme Analytics" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="domain">Domain</Label>
              <Input id="domain" value={form.domain} onChange={set("domain")} placeholder="acme.com" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="niche">Niche</Label>
              <Input id="niche" value={form.niche} onChange={set("niche")} placeholder="B2B SaaS analytics" />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="audience">Audience</Label>
            <Input id="audience" value={form.audience} onChange={set("audience")} placeholder="Data teams at mid-market SaaS" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="description">Description</Label>
            <Textarea id="description" value={form.description} onChange={set("description")} placeholder="What the brand does, voice, positioning…" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="seed_topics">Seed topics <span className="text-muted-foreground">(comma-separated)</span></Label>
            <Input id="seed_topics" value={form.seed_topics} onChange={set("seed_topics")} placeholder="product analytics, churn, retention" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="target_keywords">Target keywords <span className="text-muted-foreground">(comma-separated)</span></Label>
            <Input id="target_keywords" value={form.target_keywords} onChange={set("target_keywords")} placeholder="product analytics tools, churn rate" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="competitors">Competitor domains <span className="text-muted-foreground">(comma-separated)</span></Label>
            <Input id="competitors" value={form.competitors} onChange={set("competitors")} placeholder="mixpanel.com, amplitude.com" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="sitemap_url">Sitemap URL</Label>
            <Input id="sitemap_url" value={form.sitemap_url} onChange={set("sitemap_url")} placeholder="https://acme.com/sitemap.xml" />
          </div>

          <DialogFooter className="mt-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="gold" disabled={saving || !form.name.trim()}>
              {saving ? "Saving…" : brand ? "Save changes" : "Create brand"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
