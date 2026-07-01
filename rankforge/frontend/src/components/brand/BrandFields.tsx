"use client";

import * as React from "react";
import { toast } from "sonner";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { BusinessProfile, BusinessProfileInput } from "@/lib/api";

/** Read an image file, downscale it to a small square-ish avatar, and return a data
 *  URL — so a brand logo is self-contained (no object storage) and stays tiny. SVGs
 *  are used as-is (already small + scalable). */
async function fileToLogoDataUrl(file: File, max = 128): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error("read failed"));
    r.readAsDataURL(file);
  });
  if (file.type === "image/svg+xml") return dataUrl;
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = () => reject(new Error("decode failed"));
    im.src = dataUrl;
  });
  const scale = Math.min(1, max / Math.max(img.width, img.height));
  const w = Math.max(1, Math.round(img.width * scale));
  const h = Math.max(1, Math.round(img.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return dataUrl;
  ctx.drawImage(img, 0, 0, w, h);
  return canvas.toDataURL("image/png");
}

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
  url_pattern: string;
  default_author: string;
  logo_url: string;
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
  url_pattern: "",
  default_author: "",
  logo_url: "",
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
  url_pattern: b.url_pattern ?? "",
  default_author: b.default_author ?? "",
  logo_url: b.logo_url ?? "",
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
  url_pattern: f.url_pattern.trim() || null,
  default_author: f.default_author.trim() || null,
  logo_url: f.logo_url.trim() || null,
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

  async function onPickLogo(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error("Please pick an image under 5 MB");
      return;
    }
    try {
      onChange({ logo_url: await fileToLogoDataUrl(file) });
    } catch {
      toast.error("Couldn't read that image");
    }
  }

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
      <div className="grid gap-1.5">
        <Label htmlFor="url_pattern">
          Blog URL pattern{" "}
          <span className="text-muted-foreground">
            (where your published articles live)
          </span>
        </Label>
        <Input
          id="url_pattern"
          value={value.url_pattern}
          onChange={set("url_pattern")}
          placeholder="https://blog.acme.com/{slug}"
        />
        <p className="text-xs text-muted-foreground">
          Tokens: <code>{"{slug}"}</code>, <code>{"{id}"}</code>. Required for
          internal linking — links point here.
          {value.url_pattern.includes("{slug}") && (
            <>
              {" "}
              e.g.{" "}
              <span className="font-data">
                {value.url_pattern.replace("{slug}", "my-article")}
              </span>
            </>
          )}
        </p>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="default_author">
          Default author{" "}
          <span className="text-muted-foreground">(byline for new articles)</span>
        </Label>
        <Input
          id="default_author"
          value={value.default_author}
          onChange={set("default_author")}
          placeholder="Acme Team"
        />
        <p className="text-xs text-muted-foreground">
          The <code>author</code> in exported frontmatter. Any article can override it.
        </p>
      </div>
      <div className="grid gap-1.5">
        <Label>
          Logo{" "}
          <span className="text-muted-foreground">(shown in the brand switcher)</span>
        </Label>
        <div className="flex items-center gap-3">
          {value.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={value.logo_url}
              alt="Brand logo"
              className="size-10 shrink-0 rounded-md border border-input bg-white object-contain"
            />
          ) : (
            <div className="flex size-10 shrink-0 items-center justify-center rounded-md border border-dashed border-input text-[10px] text-muted-foreground">
              Logo
            </div>
          )}
          <label className="inline-flex h-8 cursor-pointer items-center rounded-md border border-input px-3 text-xs font-medium hover:bg-secondary">
            Upload
            <input
              type="file"
              accept="image/png,image/jpeg,image/svg+xml,image/webp"
              className="hidden"
              onChange={onPickLogo}
            />
          </label>
          {value.logo_url && (
            <button
              type="button"
              onClick={() => onChange({ logo_url: "" })}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Remove
            </button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          PNG, SVG, JPG, or WebP — downscaled to a small square avatar.
        </p>
      </div>
    </div>
  );
}
