"use client";

import { use } from "react";
import Link from "next/link";
import { Loader2, PenLine } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { useArticles } from "@/lib/hooks/useArticles";
import type { ArticleSummary } from "@/lib/api";

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
  const { data: articles, isLoading } = useArticles(id);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <div className="mb-6 flex items-center gap-2">
        <PenLine className="size-5 text-muted-foreground" />
        <h1 className="font-display text-2xl font-bold">Articles</h1>
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

      <div className="grid gap-3">
        {articles?.map((a) => (
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
                <span className="shrink-0 rounded bg-secondary px-2 py-0.5 text-xs capitalize text-muted-foreground">
                  {a.status}
                </span>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
