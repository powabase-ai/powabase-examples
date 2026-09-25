"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Plus, Save, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useGenerateFrontmatter, useUpdateArticle } from "@/lib/hooks/useArticles";
import type { Article, BlogProfile, FaqItem } from "@/lib/api";
import {
  adoptChanged,
  buildPatch,
  fromServer,
  generateConfirmMessage,
  isDirty,
  rebase,
  shouldAdoptSave,
  type FrontmatterDraft,
} from "@/lib/frontmatterDraft";
import { describeFrontmatterResult } from "@/lib/frontmatterResult";
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
  runCurrent,
}: {
  article: Article;
  profile: BlogProfile;
  /** The parent's refine/generation is running — lock the editor too. */
  busy: boolean;
  /** The last run's results still describe the article (see useRunCurrent). */
  runCurrent: boolean;
}) {
  const update = useUpdateArticle(article.id);
  const generate = useGenerateFrontmatter(article.id);

  // Draft = what the inputs show; baseline = the last server values the editor
  // adopted. Dirty/PATCH are draft-vs-baseline (see lib/frontmatterDraft.ts), so a
  // server-side change never makes an untouched draft look edited.
  const [draft, setDraft] = useState<FrontmatterDraft>(() => fromServer(article));
  const [baseline, setBaseline] = useState<FrontmatterDraft>(() => fromServer(article));
  const { category, summary, faq, metaTitle } = draft;
  const setField = <K extends keyof FrontmatterDraft>(k: K, v: FrontmatterDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));
  const setFaq = (fn: (prev: FaqItem[]) => FaqItem[]) =>
    setDraft((d) => ({ ...d, faq: fn(d.faq) }));

  const dirty = isDirty(draft, baseline);

  // A new server record (another article, or this one changed server-side: generate,
  // refine, revert, a poll, our own save) → rebase: unedited fields take the server
  // value, edited fields keep the user's text, and the baseline becomes the server
  // values. Deliberately narrow deps — `article` changes identity on every poll.
  // Also the article currently shown — a save response for another one is ignored.
  const currentArticleId = useRef(article.id);
  useEffect(() => {
    const idChanged = currentArticleId.current !== article.id;
    currentArticleId.current = article.id;
    const next = rebase(draft, baseline, fromServer(article), idChanged);
    setDraft(next.draft);
    setBaseline(next.baseline);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [article.id, article.updated_at]);

  const wc = wordCount(summary);
  const summaryOutOfRange =
    profile.summary.enabled &&
    (wc < profile.summary.min_words || wc > profile.summary.max_words);
  const titleTooLong = metaTitle.length > profile.meta.title_max;

  function save() {
    // Only the fields that differ from the baseline — an omitted key is left alone
    // server-side, and an explicit null clears it.
    update.mutate(buildPatch(draft, baseline), {
      onSuccess: (updated) => {
        // Navigated to another article meanwhile — don't write this one's values
        // into that article's editor.
        if (!shouldAdoptSave(updated.id, currentArticleId.current)) return;
        // The server's response is the new baseline — adopt it exactly so the
        // editor can't stay "dirty" against the server's normalized values.
        const saved = fromServer(updated);
        setDraft(saved);
        setBaseline(saved);
        toast.success("Frontmatter saved");
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
    });
  }

  function generateFrontmatter() {
    // Generate replaces the fields it writes even over unsaved edits — ask first.
    const confirmText = generateConfirmMessage(draft, baseline);
    if (confirmText && !window.confirm(confirmText)) return;
    // Explicit request: regenerate summary & FAQ even when the stored ones pass.
    generate.mutate(
      { force: true },
      {
        onSuccess: (result) => {
          const { article: updated, changed } = result;
          // Explicit request: the written fields replace any unsaved edit to them
          // (draft and baseline), so Save can't write the edit back over them.
          if (shouldAdoptSave(updated.id, currentArticleId.current)) {
            // Functional updates: the cache echo of this response may already
            // have rebased draft/baseline since this closure was created.
            setDraft((d) => adoptChanged(d, d, updated, changed).draft);
            setBaseline((b) => adoptChanged(b, b, updated, changed).baseline);
          }
          // Worded from `changed` + `flags` only — never claims a field that
          // wasn't written (e.g. a summary kept because the model's was too short).
          const { kind, message } = describeFrontmatterResult(result, "generate");
          toast[kind](message);
        },
        // A 409 detail (e.g. "blog profile is invalid: …") is shown as-is.
        onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
      }
    );
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
  // instructed refine (e.g. a field it couldn't safely regenerate). They describe
  // that run's output, so they're hidden once the article has been written since
  // (e.g. the user saved a fix here) — the live export check below takes over.
  const frontmatterFlags = runCurrent ? article.progress?.frontmatter_flags ?? [] : [];

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

  // The onSuccess handlers above adopt the server's response as the new local
  // state — so while either mutation is in flight, every editable control must be
  // disabled, or a keystroke landing between "request sent" and "response applied"
  // would get silently overwritten. A parent refine/generation (`busy`) locks it
  // too: its result replaces these fields.
  const formBusy = busy || update.isPending || generate.isPending;

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
          disabled={formBusy}
        >
          {generate.isPending ? <Loader2 className="animate-spin" /> : <Wand2 />}
          Generate summary &amp; FAQ
        </Button>
      </div>

      <fieldset disabled={formBusy} className="space-y-4 border-0 p-0 m-0 min-w-0">
      <div className="space-y-1.5">
        <Label htmlFor="fm-category" className="text-xs">
          Category
        </Label>
        <select
          id="fm-category"
          value={category}
          onChange={(e) => setField("category", e.target.value)}
          className="h-9 w-full rounded-md border border-input bg-card px-2 text-sm outline-none focus:ring-1 focus:ring-[rgb(var(--ember))] disabled:cursor-not-allowed disabled:opacity-50"
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
            onChange={(e) => setField("summary", e.target.value)}
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
          onChange={(e) => setField("metaTitle", e.target.value)}
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
      </fieldset>

      <Button
        size="sm"
        variant="gold"
        className="w-full"
        onClick={save}
        disabled={formBusy || !dirty}
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
          <p className="mb-1 font-medium">Flagged by the last run</p>
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
