"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { BlogProfile } from "@/lib/api";

export const DEFAULT_BLOG_PROFILE: BlogProfile = {
  categories: [{ key: "general", label: "General", description: "", technical: true }],
  summary: { enabled: true, min_words: 40, max_words: 60 },
  faq: { enabled: true, min: 3, max: 6 },
  meta: { title_max: 60, description_max: 160 },
  links: { min: 3, max: 5, trailing_slash: true, hub_pages: [] },
  stance: "neutral",
};

function Num({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <Input type="number" className="h-8 w-20" value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

export function BlogProfileForm({
  value, onChange,
}: { value: BlogProfile | null; onChange: (v: BlogProfile | null) => void }) {
  if (!value)
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">
          No blog profile. Articles export with the basic frontmatter only.
        </p>
        <Button type="button" size="sm" onClick={() => onChange(DEFAULT_BLOG_PROFILE)}>
          Enable blog profile
        </Button>
      </div>
    );
  const set = (patch: Partial<BlogProfile>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <Label>Categories</Label>
        {value.categories.map((c, i) => (
          <div key={i} className="grid grid-cols-[8rem_10rem_1fr_auto_auto] items-center gap-2">
            <Input value={c.key} placeholder="key"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, key: e.target.value } : x) })} />
            <Input value={c.label} placeholder="Label"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })} />
            <Input value={c.description} placeholder="Description"
              onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, description: e.target.value } : x) })} />
            <label className="flex items-center gap-1 text-xs">
              <input type="checkbox" checked={c.technical}
                onChange={(e) => set({ categories: value.categories.map((x, j) => j === i ? { ...x, technical: e.target.checked } : x) })} />
              technical
            </label>
            <Button type="button" variant="ghost" size="sm" aria-label="Remove category"
              disabled={value.categories.length === 1}
              onClick={() => set({ categories: value.categories.filter((_, j) => j !== i) })}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm"
          onClick={() => set({ categories: [...value.categories, { key: "", label: "", description: "", technical: true }] })}>
          <Plus className="size-4" /> Category
        </Button>
      </section>

      <section className="space-y-2">
        <Label>Hub pages (linked when a topic phrase appears)</Label>
        {value.links.hub_pages.map((h, i) => {
          const upd = (p: Partial<typeof h>) => set({ links: { ...value.links,
            hub_pages: value.links.hub_pages.map((x, j) => j === i ? { ...x, ...p } : x) } });
          return (
            <div key={i} className="grid grid-cols-[12rem_12rem_1fr_auto] items-center gap-2">
              <Input value={h.path} placeholder="/path/" onChange={(e) => upd({ path: e.target.value })} />
              <Input value={h.title} placeholder="Title" onChange={(e) => upd({ title: e.target.value })} />
              <Input value={h.topics.join(", ")} placeholder="topic one, topic two"
                onChange={(e) => upd({ topics: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })} />
              <Button type="button" variant="ghost" size="sm" aria-label="Remove hub page"
                onClick={() => set({ links: { ...value.links, hub_pages: value.links.hub_pages.filter((_, j) => j !== i) } })}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          );
        })}
        <Button type="button" variant="outline" size="sm"
          onClick={() => set({ links: { ...value.links, hub_pages: [...value.links.hub_pages, { path: "/", title: "", topics: [] }] } })}>
          <Plus className="size-4" /> Hub page
        </Button>
      </section>

      <section className="flex flex-wrap gap-4">
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.summary.enabled}
            onChange={(e) => set({ summary: { ...value.summary, enabled: e.target.checked } })} />
          Summary
        </label>
        <Num label="min words" value={value.summary.min_words} onChange={(n) => set({ summary: { ...value.summary, min_words: n } })} />
        <Num label="max words" value={value.summary.max_words} onChange={(n) => set({ summary: { ...value.summary, max_words: n } })} />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.faq.enabled}
            onChange={(e) => set({ faq: { ...value.faq, enabled: e.target.checked } })} />
          FAQ in frontmatter
        </label>
        <Num label="FAQ min" value={value.faq.min} onChange={(n) => set({ faq: { ...value.faq, min: n } })} />
        <Num label="FAQ max" value={value.faq.max} onChange={(n) => set({ faq: { ...value.faq, max: n } })} />
      </section>

      <section className="flex flex-wrap gap-4">
        <Num label="Title max" value={value.meta.title_max} onChange={(n) => set({ meta: { ...value.meta, title_max: n } })} />
        <Num label="Description max" value={value.meta.description_max} onChange={(n) => set({ meta: { ...value.meta, description_max: n } })} />
        <Num label="Links min" value={value.links.min} onChange={(n) => set({ links: { ...value.links, min: n } })} />
        <Num label="Links max" value={value.links.max} onChange={(n) => set({ links: { ...value.links, max: n } })} />
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={value.links.trailing_slash}
            onChange={(e) => set({ links: { ...value.links, trailing_slash: e.target.checked } })} />
          Trailing slash on URLs
        </label>
        <label className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Stance</span>
          <select className="h-8 rounded-md border bg-background px-2" value={value.stance}
            onChange={(e) => set({ stance: e.target.value as BlogProfile["stance"] })}>
            <option value="neutral">Neutral</option>
            <option value="favor_brand">Favor the brand</option>
          </select>
        </label>
      </section>

      <Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)}>
        Disable blog profile
      </Button>
    </div>
  );
}
