"use client";

import { useState } from "react";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PostFrontmatterEditor } from "@/components/PostFrontmatterEditor";
import { useRefineArticle, useRevertArticle } from "@/lib/hooks/useArticles";
import type { Article, BlogProfile } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The article page's "Post" tab: instruction-driven refine/rework + revert
 *  (always shown), plus the frontmatter editor and its export-check preview
 *  (only when the brand has a blog_profile). */
export function PostPanel({
  article,
  profile,
  busy,
}: {
  article: Article;
  profile: BlogProfile | null;
  busy: boolean;
}) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"refine" | "rework">("refine");
  const refine = useRefineArticle(article.id);
  const revert = useRevertArticle(article.id);
  // Guard against both the parent's coarse "generating" flag and this panel's
  // own in-flight mutations, so a second click can't race the first.
  const acting = busy || refine.isPending || revert.isPending;

  const before = (article.progress as { before?: Record<string, number | null> })
    ?.before;
  // Set (with generation_status "done") when the last instructed refine/rework
  // produced nothing usable — the article body is unchanged. Shown until the next
  // run's progress no longer carries it, rather than a one-shot error toast.
  const refineError =
    article.generation_status === "done" ? article.progress?.refine_error : null;

  return (
    <div className="space-y-4">
      {refineError && (
        <div className="rounded-md border border-[rgb(var(--ember))]/40 bg-[rgb(var(--ember))]/5 p-2.5 text-xs text-muted-foreground">
          Refine didn&apos;t apply: {refineError}. Your article is unchanged.
        </div>
      )}
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Refine with instructions
        </p>
        <Textarea
          value={text}
          maxLength={4000}
          rows={5}
          placeholder="e.g. Rewrite the intro around agent memory and cut the history section."
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-1.5 flex items-center justify-between text-xs text-muted-foreground">
          <span>{text.length}/4000</span>
          <div className="inline-flex rounded-md border">
            {(["refine", "rework"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  "px-3 py-1",
                  mode === m && "bg-muted font-semibold text-foreground"
                )}
              >
                {m === "refine" ? "Refine" : "Rework"}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {mode === "refine"
            ? "Light edit: keeps the outline and headings, changes only what you ask."
            : "May restructure, re-outline or change the angle, grounded in the article's sources."}
        </p>
        <Button
          size="sm"
          variant="gold"
          className="mt-2 w-full"
          disabled={acting || !text.trim()}
          onClick={() =>
            refine.mutate(
              { instructions: text, mode },
              {
                onSuccess: () => {
                  toast.success(mode === "refine" ? "Refining…" : "Reworking…");
                  setText("");
                },
                onError: (e) =>
                  toast.error(e instanceof Error ? e.message : "Failed"),
              }
            )
          }
        >
          {refine.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
          Run
        </Button>

        {before && article.generation_status === "done" && (
          <div className="mt-2 rounded-md border p-2 text-xs">
            {(["seo", "geo", "readability"] as const).map((k) => {
              const now = (
                article[`${k}_score` as const] as { total?: number } | null
              )?.total;
              return (
                <span key={k} className="mr-3 font-data">
                  {k.toUpperCase()} {before[k] ?? "–"} → {now ?? "–"}
                </span>
              );
            })}
          </div>
        )}

        <Button
          size="sm"
          variant="outline"
          className="mt-2 w-full"
          disabled={acting}
          onClick={() => {
            if (!window.confirm("Revert to the previous version?")) return;
            revert.mutate(undefined, {
              onSuccess: () => toast.success("Reverted"),
              onError: (e) =>
                toast.error(e instanceof Error ? e.message : "Nothing to revert"),
            });
          }}
        >
          {revert.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />}
          Revert to previous version
        </Button>
      </div>

      {profile && (
        <PostFrontmatterEditor article={article} profile={profile} busy={busy} />
      )}
    </div>
  );
}
