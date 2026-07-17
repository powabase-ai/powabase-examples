"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, Search, Share2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Page, PageHeader } from "@/components/layout/PageHeader";
import { LinkedInPostCard } from "@/components/LinkedInPostCard";
import { ANGLES, type Angle } from "@/lib/api";
import { useArticles } from "@/lib/hooks/useArticles";
import {
  useBrandLinkedInPosts,
  useGenerateLinkedInPost,
} from "@/lib/hooks/useLinkedIn";

/** Social — turn the brand's articles into LinkedIn posts. Master-detail: a searchable
 *  article rail on the left (scales to a big library), the selected article's post
 *  variants + generate controls in the main region. Workspace-shared like all content. */
export default function SocialPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const articles = useArticles(id);
  const posts = useBrandLinkedInPosts(id);

  const [picked, setPicked] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [angle, setAngle] = useState<Angle>("key_insight");

  // Only articles with a finished draft can be repurposed.
  const ready = useMemo(
    () => (articles.data ?? []).filter((a) => a.generation_status === "done"),
    [articles.data]
  );

  // Post count per article (drives the rail badges and the detail list).
  const byArticle = useMemo(() => {
    const map = new Map<string, number>();
    for (const p of posts.data ?? []) {
      map.set(p.article_id, (map.get(p.article_id) ?? 0) + 1);
    }
    return map;
  }, [posts.data]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return ready;
    return ready.filter((a) => a.title.toLowerCase().includes(needle));
  }, [ready, q]);

  // Default selection: first article that already has posts, else the first article.
  // Derived (no effect): an explicit click always wins.
  const activeId =
    picked ?? ready.find((a) => (byArticle.get(a.id) ?? 0) > 0)?.id ?? ready[0]?.id ?? null;
  const active = ready.find((a) => a.id === activeId) ?? null;
  const activePosts = useMemo(
    () => (posts.data ?? []).filter((p) => p.article_id === activeId),
    [posts.data, activeId]
  );

  const generate = useGenerateLinkedInPost(activeId ?? "");

  function onGenerate() {
    generate.mutate(angle, {
      onSuccess: () => toast.success("LinkedIn post generated"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Generation failed"),
    });
  }

  return (
    <Page>
      <PageHeader
        icon={Share2}
        title="Social"
        meta={
          posts.data?.length
            ? `${posts.data.length} LinkedIn post${posts.data.length === 1 ? "" : "s"} across ${byArticle.size} article${byArticle.size === 1 ? "" : "s"}`
            : "LinkedIn posts forged from your articles"
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr]">
        {/* Article rail */}
        <div className="flex min-h-0 flex-col border-r border-border">
          <div className="border-b border-border p-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search articles…"
                className="h-8 pl-7 text-sm"
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {articles.isLoading ? (
              <p className="p-4 text-sm text-muted-foreground">Loading…</p>
            ) : articles.error ? (
              <p className="p-4 text-sm text-destructive">
                Couldn&apos;t load articles —{" "}
                {articles.error instanceof Error
                  ? articles.error.message
                  : "please retry"}
                .
              </p>
            ) : ready.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">
                No generated articles yet — draft an article first, then turn it
                into LinkedIn posts here.
              </p>
            ) : filtered.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">
                No articles match “{q}”.
              </p>
            ) : (
              <ul>
                {filtered.map((a) => {
                  const count = byArticle.get(a.id) ?? 0;
                  return (
                    <li key={a.id}>
                      <button
                        onClick={() => setPicked(a.id)}
                        className={`w-full border-b border-border px-4 py-3 text-left transition-colors ${
                          activeId === a.id
                            ? "bg-[rgb(var(--accent-gold-muted))]"
                            : "hover:bg-secondary"
                        }`}
                      >
                        <div className="line-clamp-2 text-sm font-medium">
                          {a.title}
                        </div>
                        <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
                          <span className="rounded bg-secondary px-1.5 py-0.5 capitalize">
                            {a.status.replace(/_/g, " ")}
                          </span>
                          <span
                            className={
                              count > 0
                                ? "inline-flex items-center gap-1 text-[rgb(var(--ember))]"
                                : "inline-flex items-center gap-1"
                            }
                          >
                            <Share2 className="size-3" />
                            {count} post{count === 1 ? "" : "s"}
                          </span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Selected article's posts */}
        <div className="min-h-0 overflow-y-auto">
          {!active ? (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
              Select an article on the left to see and generate its LinkedIn
              posts.
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6">
              {/* Article header */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <Link
                    href={`/brands/${id}/articles/${active.id}`}
                    className="inline-flex items-center gap-1.5 font-display text-lg font-bold hover:underline"
                  >
                    <span className="line-clamp-2">{active.title}</span>
                    <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
                  </Link>
                  <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded bg-secondary px-1.5 py-0.5 capitalize">
                      {active.status.replace(/_/g, " ")}
                    </span>
                    <span>
                      {activePosts.length} post
                      {activePosts.length === 1 ? "" : "s"}
                    </span>
                    {active.status !== "published" && (
                      <span>· article link added once published</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Generate controls */}
              <div className="flex items-center gap-2 rounded-md border border-border bg-card p-2.5">
                <select
                  value={angle}
                  onChange={(e) => setAngle(e.target.value as Angle)}
                  className="h-9 flex-1 rounded-md border border-input bg-background px-2 text-sm"
                  title="The angle shapes the post's hook and framing"
                >
                  {ANGLES.map((a) => (
                    <option key={a.slug} value={a.slug}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <Button
                  variant="gold"
                  size="sm"
                  className="h-9"
                  onClick={onGenerate}
                  disabled={generate.isPending}
                >
                  {generate.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Sparkles />
                  )}
                  Generate variant
                </Button>
              </div>
              <p className="-mt-2 text-[11px] text-muted-foreground">
                Uses credits. Brand voice, scroll-stopping hook above the fold;
                each generation adds a new editable variant below.
              </p>

              {/* Variants */}
              {posts.isLoading ? (
                <p className="text-sm text-muted-foreground">Loading posts…</p>
              ) : posts.error ? (
                <div className="rounded-xl border border-dashed border-destructive/40 p-8 text-center text-sm text-destructive">
                  Couldn&apos;t load posts for this article —{" "}
                  {posts.error instanceof Error
                    ? posts.error.message
                    : "please retry"}
                  . Existing variants may not be shown, so hold off on regenerating.
                </div>
              ) : activePosts.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                  No posts for this article yet — pick an angle and generate the
                  first variant.
                </div>
              ) : (
                <ul className="space-y-3">
                  {activePosts.map((p) => (
                    <li key={p.id}>
                      <LinkedInPostCard articleId={p.article_id} post={p} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </Page>
  );
}
