"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Markdown } from "@/components/Markdown";
import { useArticle } from "@/lib/hooks/useArticles";

const PHASE_LABEL: Record<string, string> = {
  grounding: "Grounding in research sources…",
  outlining: "Outlining…",
  drafting: "Drafting sections…",
  optimizing: "Optimizing…",
  queued: "Queued…",
};

export default function ArticleView({
  params,
}: {
  params: Promise<{ id: string; articleId: string }>;
}) {
  const { id, articleId } = use(params);
  const { data: a, isLoading } = useArticle(articleId);

  const generating =
    a && !["done", "failed"].includes(a.generation_status);

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <Link
        href={`/brands/${id}/articles`}
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Articles
      </Link>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {a && (
        <>
          <h1 className="font-display text-3xl font-bold leading-tight">{a.title}</h1>
          {a.meta_description && (
            <p className="mt-2 text-sm text-muted-foreground">{a.meta_description}</p>
          )}

          {generating && (
            <Card className="mt-6">
              <CardContent className="flex items-center gap-3 py-5 text-sm">
                <Loader2 className="size-4 animate-spin text-[rgb(var(--ember-bright))]" />
                <span>
                  {PHASE_LABEL[a.generation_status] ?? a.generation_status}
                  {a.generation_status === "drafting" && a.progress?.total
                    ? ` (${a.progress.done ?? 0}/${a.progress.total})`
                    : ""}
                </span>
              </CardContent>
            </Card>
          )}

          {a.generation_status === "failed" && (
            <Card className="mt-6">
              <CardContent className="py-5 text-sm text-destructive">
                Generation failed: {a.generation_error}
              </CardContent>
            </Card>
          )}

          {a.content_md && (
            <article className="mt-8">
              <Markdown>{a.content_md}</Markdown>
            </article>
          )}
        </>
      )}
    </div>
  );
}
