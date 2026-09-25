"use client";

import Link from "next/link";
import { Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { PostFrontmatterEditor } from "@/components/PostFrontmatterEditor";
import { useRevertArticle } from "@/lib/hooks/useArticles";
import type { Article, BlogProfile } from "@/lib/api";

/** The article page's "Post" tab: a compact "Last run" area (the last instructed
 *  refine/rework's outcome, when there is one, plus revert — always shown), and
 *  the frontmatter editor with its export-check preview (only when the brand has
 *  a blog_profile). "Refine with instructions" itself lives in the page's top
 *  action bar (RefineInstructionsDialog), not here. */
export function PostPanel({
  article,
  profile,
  profileInvalid,
  brandId,
  busy,
  runCurrent,
}: {
  article: Article;
  profile: BlogProfile | null;
  /** The brand has a stored blog profile that doesn't conform (blogProfileState
   *  "invalid"): generation runs without its rules, so say so. */
  profileInvalid: boolean;
  brandId: string;
  busy: boolean;
  /** The last run's results (score strip, frontmatter flags) still describe the
   *  article — false once it has been written since (edit, save, revert…); see
   *  useRunCurrent. */
  runCurrent: boolean;
}) {
  const revert = useRevertArticle(article.id);
  // Guard against both the parent's coarse "generating" flag and this panel's
  // own in-flight mutation, so a second click can't race the first.
  const acting = busy || revert.isPending;

  const before = (article.progress as { before?: Record<string, number | null> })
    ?.before;
  // Set (with generation_status "done") when the last instructed refine/rework
  // produced nothing usable — the article body is unchanged. Shown until the next
  // run's progress no longer carries it, rather than a one-shot error toast.
  const refineError =
    article.generation_status === "done" ? article.progress?.refine_error : null;
  // The frontmatter editor shows the last run's frontmatter flags; when it isn't
  // rendered (no or invalid profile) they're shown here instead — e.g. the
  // "blog profile is invalid: …" flag a generation leaves.
  const orphanFlags =
    !profile && runCurrent ? article.progress?.frontmatter_flags ?? [] : [];

  return (
    <div className="space-y-4">
      {profileInvalid && (
        <div className="rounded-md border border-[rgb(var(--destructive))]/40 bg-[rgb(var(--destructive))]/5 p-2.5 text-xs">
          This brand&apos;s blog profile is invalid — fix it in{" "}
          <Link href={`/brands/${brandId}/settings`} className="font-medium underline">
            Settings
          </Link>
          . Until then, generation and refine ignore its rules and the frontmatter
          editor is hidden.
        </div>
      )}

      <div className="space-y-2">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Last run
        </p>
        {orphanFlags.length > 0 && (
          <div className="rounded-md border border-[rgb(var(--ember))]/40 bg-[rgb(var(--ember))]/5 p-2 text-xs">
            <p className="mb-1 font-medium">Flagged by the last run</p>
            <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
              {orphanFlags.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}
        {refineError && (
          <div className="rounded-md border border-[rgb(var(--ember))]/40 bg-[rgb(var(--ember))]/5 p-2.5 text-xs text-muted-foreground">
            Refine didn&apos;t apply: {refineError}. Your article is unchanged.
          </div>
        )}
        {before && runCurrent && (
          <div className="rounded-md border p-2 text-xs">
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
          className="w-full"
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
        <PostFrontmatterEditor
          article={article}
          profile={profile}
          busy={busy}
          runCurrent={runCurrent}
        />
      )}
    </div>
  );
}
