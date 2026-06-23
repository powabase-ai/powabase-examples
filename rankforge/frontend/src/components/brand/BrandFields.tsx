"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

export interface BrandFormState {
  name: string;
  domain: string;
  niche: string;
  audience: string;
  description: string;
  seed_topics: string;
  target_keywords: string;
  competitors: string;
  sitemap_url: string;
}

const csv = (xs?: string[]) => (xs ?? []).join(", ");
const fromCsv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

export const emptyBrandForm = (): BrandFormState => ({
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

export const brandToForm = (b: BusinessProfile): BrandFormState => ({
  name: b.name ?? "",
  domain: b.domain ?? "",
  niche: b.niche ?? "",
  audience: b.audience ?? "",
  description: b.description ?? "",
  seed_topics: csv(b.seed_topics),
  target_keywords: csv(b.target_keywords),
  competitors: csv(b.competitors?.map((c) => c.domain)),
  sitemap_url: b.sitemap_url ?? "",
});

export const formToPayload = (f: BrandFormState): BusinessProfileInput => ({
  name: f.name.trim(),
  domain: f.domain.trim() || null,
  niche: f.niche.trim() || null,
  audience: f.audience.trim() || null,
  description: f.description.trim() || null,
  seed_topics: fromCsv(f.seed_topics),
  target_keywords: fromCsv(f.target_keywords),
  competitors: fromCsv(f.competitors).map((domain) => ({ domain })),
  sitemap_url: f.sitemap_url.trim() || null,
});

export function BrandFields({
  value,
  onChange,
}: {
  value: BrandFormState;
  onChange: (patch: Partial<BrandFormState>) => void;
}) {
  const set =
    (k: keyof BrandFormState) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onChange({ [k]: e.target.value });

  return (
    <div className="grid gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="name">Name *</Label>
        <Input id="name" required value={value.name} onChange={set("name")} placeholder="Acme Analytics" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="domain">Domain</Label>
          <Input id="domain" value={value.domain} onChange={set("domain")} placeholder="acme.com" />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="niche">Niche</Label>
          <Input id="niche" value={value.niche} onChange={set("niche")} placeholder="B2B SaaS analytics" />
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="audience">Audience</Label>
        <Input id="audience" value={value.audience} onChange={set("audience")} placeholder="Data teams at mid-market SaaS" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" value={value.description} onChange={set("description")} placeholder="What the brand does, voice, positioning…" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="seed_topics">Seed topics <span className="text-muted-foreground">(comma-separated)</span></Label>
        <Input id="seed_topics" value={value.seed_topics} onChange={set("seed_topics")} placeholder="product analytics, churn, retention" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="target_keywords">Target keywords <span className="text-muted-foreground">(comma-separated)</span></Label>
        <Input id="target_keywords" value={value.target_keywords} onChange={set("target_keywords")} placeholder="product analytics tools, churn rate" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="competitors">Competitor domains <span className="text-muted-foreground">(comma-separated)</span></Label>
        <Input id="competitors" value={value.competitors} onChange={set("competitors")} placeholder="mixpanel.com, amplitude.com" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="sitemap_url">Sitemap URL</Label>
        <Input id="sitemap_url" value={value.sitemap_url} onChange={set("sitemap_url")} placeholder="https://acme.com/sitemap.xml" />
      </div>
    </div>
  );
}
