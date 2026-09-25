"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Plus, Save, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useGenerateFrontmatter, useUpdateArticle } from "@/lib/hooks/useArticles";
import type { Article, ArticleUpdate, BlogProfile, FaqItem } from "@/lib/api";
import { cn } from "@/lib/utils";

function wordCount(s: string): number {
  const t = s.trim();
  return t ? t.split(/\s+/).length : 0;
}

/** The frontmatter editor half of PostPanel — category, summary, FAQ, meta title,
 *  and a live export-check preview. Split out of PostPanel.tsx to keep that file
 *  focused; only rendered when the brand has a blog_profile. */
export function PostFrontmatterEditor({
  article,
  profile,
  busy,
}: {
  article: Article;
  profile: BlogProfile;
  busy: boolean;
}) {
  const update = useUpdateArticle(article.id);
  const generate = useGenerateFrontmatter(article.id);

  const [category, setCategory] = useState(article.category ?? "");
  const [summary, setSummary] = useState(article.summary ?? "");
  const [faq, setFaq] = useState<FaqItem[]>(article.faq ?? []);
  const [metaTitle, setMetaTitle] = useState(article.meta_title ?? "");

  // Normalize before comparing/sending — the backend strips whitespace from FAQ
  // text (and trims summary/meta title), so comparing the raw draft against the
  // server's trimmed value would leave the editor "dirty" forever after a save
  // (e.g. typing "Why? " saves as "Why?", the raw draft never matches again, and
  // the reset effect below then refuses to ever resync this field again).
  const trimmedSummary = summary.trim();
  const trimmedMetaTitle = metaTitle.trim();
  // Fully empty rows are dropped; a half-filled row is still kept (trimmed) so the
  // server rejects it rather than silently losing the typed half.
  const cleanedFaq = faq
    .map((f) => ({ q: f.q.trim(), a: f.a.trim() }))
    .filter((f) => f.q || f.a);

  const categoryChanged = category !== (article.category ?? "");
  const summaryChanged = trimmedSummary !== (article.summary ?? "");
  const faqChanged = JSON.stringify(cleanedFaq) !== JSON.stringify(article.faq ?? []);
  const metaTitleChanged = trimmedMetaTitle !== (article.meta_title ?? "");
  const dirty = categoryChanged || summaryChanged || faqChanged || metaTitleChanged;

  // Reset the editor from the server record when a different article lands, or when
  // this one has no unsaved changes (e.g. a background poll picked up a refine/
  // generation result). Skipping the reset while dirty keeps in-progress edits from
  // being wiped out mid-poll. Deliberately narrow deps — re-seeding on every
  // keystroke-triggered `article` reference change would otherwise clobber the draft
  // (same reset-on-identity-change pattern as settings/page.tsx and BrandForm).
  const prevArticleId = useRef(article.id);
  useEffect(() => {
    const articleChanged = prevArticleId.current !== article.id;
    prevArticleId.current = article.id;
    if (!articleChanged && dirty) return;
    setCategory(article.category ?? "");
    setSummary(article.summary ?? "");
    setFaq(article.faq ?? []);
    setMetaTitle(article.meta_title ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [article.id, article.updated_at]);

  const wc = wordCount(summary);
  const summaryOutOfRange =
    profile.summary.enabled &&
    (wc < profile.summary.min_words || wc > profile.summary.max_words);
  const titleTooLong = metaTitle.length > profile.meta.title_max;

  function save() {
    // Only send fields the user actually changed — an omitted key is left alone
    // server-side, and an explicit null clears it (so a cleared category/summary/
    // FAQ/meta title isn't silently dropped as a no-op).
    const payload: ArticleUpdate = {};
    if (categoryChanged) payload.category = category || null;
    if (summaryChanged) payload.summary = trimmedSummary || null;
    if (faqChanged) payload.faq = cleanedFaq.length ? cleanedFaq : null;
    if (metaTitleChanged) payload.meta_title = trimmedMetaTitle || null;

    update.mutate(payload, {
      onSuccess: (updated) => {
        // The server's response is the new baseline — adopt it exactly (rather than
        // relying on the next poll) so the editor can't stay "dirty" comparing our
        // pre-save draft against the server's normalized values.
        setCategory(updated.category ?? "");
        setSummary(updated.summary ?? "");
        setFaq(updated.faq ?? []);
        setMetaTitle(updated.meta_title ?? "");
        toast.success("Frontmatter saved");
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
    });
  }

  function generateFrontmatter() {
    generate.mutate(undefined, {
      onSuccess: ({ export_issues }) => {
        if (export_issues.length > 0) {
          toast.warning(
            `Generated — ${export_issues.length} issue${
              export_issues.length === 1 ? "" : "s"
            } remain`
          );
        } else {
          toast.success("Summary & FAQ generated");
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
    });
  }

  function updateFaq(i: number, patch: Partial<FaqItem>) {
    setFaq((prev) => prev.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  }
  function removeFaq(i: number) {
    setFaq((prev) => prev.filter((_, j) => j !== i));
  }
  function moveFaq(i: number, dir: -1 | 1) {
    setFaq((prev) => {
      const j = i + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  // Flags the server's frontmatter step left unresolved on the last generation or
  // instructed refine (e.g. a field it couldn't safely regenerate).
  const frontmatterFlags = article.progress?.frontmatter_flags ?? [];

  // Length/count rules only, mirroring blog_rules.export_issues on the server —
  // the regex-based body-FAQ check is left to the server's authoritative 422.
  const warnings: string[] = [];
  const effectiveTitle =
    article.title.length > profile.meta.title_max && metaTitle.trim()
      ? metaTitle.trim()
      : article.title;
  if (effectiveTitle.length > profile.meta.title_max) {
    warnings.push(
      `title is ${effectiveTitle.length} characters (max ${profile.meta.title_max}); ` +
        "set a shorter meta title"
    );
  }
  const descLen = (article.meta_description ?? "").length;
  if (descLen > profile.meta.description_max) {
    warnings.push(
      `description is ${descLen} characters (max ${profile.meta.description_max})`
    );
  }
  if (summaryOutOfRange) {
    warnings.push(
      `summary is ${wc} words (needs ${profile.summary.min_words}-` +
        `${profile.summary.max_words})`
    );
  }
  // Count only complete rows, as the server's clean_faq does.
  const faqCount = faq.filter((f) => f.q.trim() && f.a.trim()).length;
  if (
    profile.faq.enabled &&
    (faqCount < profile.faq.min || faqCount > profile.faq.max)
  ) {
    warnings.push(
      `faq has ${faqCount} item(s) (needs ${profile.faq.min}-${profile.faq.max})`
    );
  }

  return (
    <div className="space-y-4 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Frontmatter
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={generateFrontmatter}
          disabled={busy || generate.isPending}
        >
          {generate.isPending ? <Loader2 className="animate-spin" /> : <Wand2 />}
          Generate summary &amp; FAQ
        </Button>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="fm-category" className="text-xs">
          Category
        </Label>
        <select
          id="fm-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="h-9 w-full rounded-md border border-input bg-card px-2 text-sm outline-none focus:ring-1 focus:ring-[rgb(var(--ember))]"
        >
          <option value="">— none —</option>
          {profile.categories.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      {profile.summary.enabled && (
        <div className="space-y-1.5">
          <Label htmlFor="fm-summary" className="text-xs">
            Summary
          </Label>
          <Textarea
            id="fm-summary"
            rows={3}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
          />
          <p
            className={cn(
              "text-xs text-muted-foreground",
              summaryOutOfRange && "text-destructive"
            )}
          >
            {wc} words (needs {profile.summary.min_words}-{profile.summary.max_words})
          </p>
        </div>
      )}

      {profile.faq.enabled && (
        <div className="space-y-2">
          <Label className="text-xs">FAQ</Label>
          {faq.map((f, i) => (
            <div key={i} className="space-y-1.5 rounded-md border border-border p-2">
              <div className="flex items-center gap-1.5">
                <Input
                  value={f.q}
                  placeholder="Question"
                  onChange={(e) => updateFaq(i, { q: e.target.value })}
                  className="h-8 text-sm"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Move question up"
                  disabled={i === 0}
                  onClick={() => moveFaq(i, -1)}
                >
                  <ArrowUp className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Move question down"
                  disabled={i === faq.length - 1}
                  onClick={() => moveFaq(i, 1)}
                >
                  <ArrowDown className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Remove question"
                  onClick={() => removeFaq(i)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
              <Textarea
                value={f.a}
                placeholder="Answer"
                rows={2}
                onChange={(e) => updateFaq(i, { a: e.target.value })}
              />
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={faq.length >= profile.faq.max}
            onClick={() => setFaq((prev) => [...prev, { q: "", a: "" }])}
          >
            <Plus className="size-3.5" /> Add question
          </Button>
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="fm-meta-title" className="text-xs">
          Meta title
        </Label>
        <Input
          id="fm-meta-title"
          value={metaTitle}
          onChange={(e) => setMetaTitle(e.target.value)}
          className="h-8 text-sm"
        />
        <p
          className={cn(
            "text-xs text-muted-foreground",
            titleTooLong && "text-destructive"
          )}
        >
          {metaTitle.length}/{profile.meta.title_max}
        </p>
      </div>

      <Button
        size="sm"
        variant="gold"
        className="w-full"
        onClick={save}
        disabled={update.isPending || !dirty}
      >
        {update.isPending ? <Loader2 className="animate-spin" /> : <Save />}
        Save
      </Button>

      {warnings.length > 0 && (
        <div className="rounded-md border border-[rgb(var(--ember))]/40 bg-[rgb(var(--ember))]/5 p-2 text-xs">
          <p className="mb-1 font-medium">Export check</p>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {frontmatterFlags.length > 0 && (
        <div className="rounded-md border border-[rgb(var(--ember))]/40 bg-[rgb(var(--ember))]/5 p-2 text-xs">
          <p className="mb-1 font-medium">Flagged by the last generation</p>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {frontmatterFlags.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
