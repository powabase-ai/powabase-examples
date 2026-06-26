"use client";

import { use, useState } from "react";
import Link from "next/link";
import { Loader2, PenLine, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useArticles, useDeleteArticle } from "@/lib/hooks/useArticles";
import { useAuth } from "@/lib/auth/AuthProvider";
import {
  ARTICLE_STATUSES,
  canApprove,
  type ArticleStatus,
  type ArticleSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

function DeleteArticleButton({
  brandId,
  article,
}: {
  brandId: string;
  article: ArticleSummary;
}) {
  const del = useDeleteArticle(brandId);
  return (
    <button
      type="button"
      title="Delete article"
      onClick={(e) => {
        // The row is a Link — don't navigate when deleting.
        e.preventDefault();
        e.stopPropagation();
        if (
          !window.confirm(
            `Permanently delete “${article.title}”? This removes its versions, ` +
              "comments, internal links, and any publication records."
          )
        )
          return;
        del.mutate(article.id, {
          onSuccess: () => toast.success("Article deleted"),
          onError: (err) =>
            toast.error(err instanceof Error ? err.message : "Failed"),
        });
      }}
      disabled={del.isPending}
      className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-destructive"
    >
      {del.isPending ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Trash2 className="size-4" />
      )}
    </button>
  );
}

function GenBadge({ a }: { a: ArticleSummary }) {
  if (a.generation_status === "done")
    return <span className="text-[rgb(var(--success))]">● ready</span>;
  if (a.generation_status === "failed")
    return <span className="text-destructive">● failed</span>;
  const label =
    a.generation_status === "drafting"
      ? `drafting ${a.progress?.done ?? 0}/${a.progress?.total ?? "?"}`
      : a.generation_status;
  return (
    <span className="inline-flex items-center gap-1 text-[rgb(var(--ember-bright))]">
      <Loader2 className="size-3 animate-spin" /> {label}
    </span>
  );
}

export default function ArticlesList({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data: articles, isLoading } = useArticles(id);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<ArticleStatus | "all">("all");

  const filtered = articles?.filter(
    (a) =>
      (status === "all" || a.status === status) &&
      a.title.toLowerCase().includes(q.toLowerCase())
  );

  return (
    <Page>
      <PageHeader icon={PenLine} title="Articles" />
      <PageBody>
      <div className="mb-5 flex flex-col gap-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search articles…"
            className="pl-8"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(["all", ...ARTICLE_STATUSES] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors",
                status === s
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-muted-foreground hover:text-foreground"
              )}
            >
              {s === "all" ? "All" : s.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {articles?.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No articles yet. Generate a brief in Research, then click{" "}
            <span className="font-medium">Generate draft</span>.
          </CardContent>
        </Card>
      )}
      {articles && articles.length > 0 && filtered?.length === 0 && (
        <p className="text-sm text-muted-foreground">No articles match.</p>
      )}

      <div className="grid gap-3">
        {filtered?.map((a) => (
          <Link key={a.id} href={`/brands/${id}/articles/${a.id}`}>
            <Card className="transition-colors hover:border-[rgb(var(--ember))]/40">
              <CardContent className="flex items-center justify-between gap-4 py-4">
                <div className="min-w-0">
                  <div className="line-clamp-1 font-medium">{a.title}</div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                    <GenBadge a={a} />
                    {a.progress?.word_count ? (
                      <span>
                        <span className="font-data">{a.progress.word_count}</span> words
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="rounded bg-secondary px-2 py-0.5 text-xs capitalize text-muted-foreground">
                    {a.status.replace(/_/g, " ")}
                  </span>
                  {canEdit && <DeleteArticleButton brandId={id} article={a} />}
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
      </PageBody>
    </Page>
  );
}
