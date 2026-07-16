"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, Share2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import { LinkedInPostCard } from "@/components/LinkedInPostCard";
import {
  ANGLES,
  type Angle,
  type ArticleSummary,
  type LinkedInPostWithArticle,
} from "@/lib/api";
import { useArticles } from "@/lib/hooks/useArticles";
import {
  useBrandLinkedInPosts,
  useGenerateLinkedInPost,
} from "@/lib/hooks/useLinkedIn";

/** Social — turn the brand's articles into LinkedIn posts. Pick a source article and
 *  an angle to generate a variant; every post stays associated with (and grouped
 *  under) the article it came from. Workspace-shared like all content. */
export default function SocialPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const articles = useArticles(id);
  const posts = useBrandLinkedInPosts(id);

  const [articleId, setArticleId] = useState("");
  const [angle, setAngle] = useState<Angle>("key_insight");
  const [filter, setFilter] = useState("all");
  const generate = useGenerateLinkedInPost(articleId);

  // Only articles whose draft exists can be repurposed.
  const ready = useMemo(
    () =>
      (articles.data ?? []).filter((a) => a.generation_status === "done"),
    [articles.data]
  );

  // Group posts by their source article, preserving the server's ordering
  // (newest articles first, newest posts first within each).
  const groups = useMemo(() => {
    const map = new Map<
      string,
      { title: string; status: string; posts: LinkedInPostWithArticle[] }
    >();
    for (const p of posts.data ?? []) {
      const g = map.get(p.article_id) ?? {
        title: p.article_title,
        status: p.article_status,
        posts: [],
      };
      g.posts.push(p);
      map.set(p.article_id, g);
    }
    return [...map.entries()];
  }, [posts.data]);

  const shown =
    filter === "all" ? groups : groups.filter(([aid]) => aid === filter);

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
        meta="LinkedIn posts forged from your articles"
      />
      <PageBody>
        <div className="space-y-6">
          {/* Generate controls */}
          <div className="space-y-3 rounded-xl border border-border bg-card p-4 shadow-sm">
            <p className="text-sm font-medium">Generate a LinkedIn post</p>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
              <select
                value={articleId}
                onChange={(e) => setArticleId(e.target.value)}
                className="h-9 min-w-0 rounded-md border border-input bg-background px-2 text-sm"
              >
                <option value="">
                  {articles.isLoading
                    ? "Loading articles…"
                    : ready.length
                      ? "Choose a source article…"
                      : "No generated articles yet"}
                </option>
                {ready.map((a: ArticleSummary) => (
                  <option key={a.id} value={a.id}>
                    {a.title}
                    {a.status === "published" ? "  (published)" : ""}
                  </option>
                ))}
              </select>
              <select
                value={angle}
                onChange={(e) => setAngle(e.target.value as Angle)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
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
                disabled={!articleId || generate.isPending}
              >
                {generate.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Sparkles />
                )}
                Generate
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Uses credits. Written in your brand voice with a scroll-stopping
              hook above the fold; each generation adds a new editable variant
              under its source article. A link to the article is included only
              once it&apos;s published.
            </p>
          </div>

          {/* Filter */}
          {groups.length > 1 && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-muted-foreground">Show</label>
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs"
              >
                <option value="all">All articles ({groups.length})</option>
                {groups.map(([aid, g]) => (
                  <option key={aid} value={aid}>
                    {g.title}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Posts grouped by source article */}
          {posts.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading posts…</p>
          ) : shown.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              No posts yet — choose a source article and an angle above, then
              generate your first variant.
            </div>
          ) : (
            <div className="space-y-6">
              {shown.map(([aid, g]) => (
                <section key={aid} className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/brands/${id}/articles/${aid}`}
                      className="inline-flex min-w-0 items-center gap-1.5 font-medium hover:underline"
                    >
                      <span className="truncate">{g.title}</span>
                      <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
                    </Link>
                    <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-xs capitalize text-muted-foreground">
                      {g.status.replace(/_/g, " ")}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {g.posts.length} post{g.posts.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <ul className="space-y-3">
                    {g.posts.map((p) => (
                      <li key={p.id}>
                        <LinkedInPostCard articleId={aid} post={p} />
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </div>
      </PageBody>
    </Page>
  );
}
