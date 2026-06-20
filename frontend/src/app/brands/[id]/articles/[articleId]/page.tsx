"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Markdown } from "@/components/Markdown";
import { useArticle, useScoreArticle } from "@/lib/hooks/useArticles";
import type { Score, ScoreSignal } from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  grounding: "Grounding in research sources…",
  outlining: "Outlining…",
  drafting: "Drafting sections…",
  optimizing: "Scoring SEO & GEO…",
  queued: "Queued…",
};

function ScoreCard({ name, score }: { name: string; score: Score }) {
  const color = score.met ? "var(--success)" : "var(--ember)";
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <span className="font-display text-sm font-bold uppercase tracking-wide">
          {name}
        </span>
        <span
          className="font-data text-sm font-semibold"
          style={{ color: `rgb(${color})` }}
        >
          {score.total}
          <span className="text-muted-foreground">/{score.target}</span>
        </span>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-secondary">
          <div
            className="h-full rounded-full"
            style={{ width: `${score.total}%`, background: `rgb(${color})` }}
          />
        </div>
        <div>
          {score.signals.map((s: ScoreSignal) => (
            <div
              key={s.key}
              className="flex items-start gap-3 border-t border-border py-2 first:border-0"
            >
              <span className="w-8 shrink-0 font-data text-sm">{s.score}</span>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  {s.label}
                  {s.method === "llm" && (
                    <Sparkles className="size-3 text-[rgb(var(--ember))]" />
                  )}
                </div>
                <div className="text-xs text-muted-foreground">{s.explanation}</div>
                {s.fixes.map((f, i) => (
                  <div key={i} className="text-xs text-[rgb(var(--ember-deep))]">
                    → {f}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ArticleView({
  params,
}: {
  params: Promise<{ id: string; articleId: string }>;
}) {
  const { id, articleId } = use(params);
  const { data: a, isLoading } = useArticle(articleId);
  const rescore = useScoreArticle(articleId);

  const generating = a && !["done", "failed"].includes(a.generation_status);

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <div className="mb-4 flex items-center justify-between">
        <Link
          href={`/brands/${id}/articles`}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> Articles
        </Link>
        {a && a.generation_status === "done" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => rescore.mutate()}
            disabled={rescore.isPending}
          >
            {rescore.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
            Re-score
          </Button>
        )}
      </div>

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

          {(a.seo_score || a.geo_score) && (
            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              {a.seo_score && <ScoreCard name="SEO" score={a.seo_score} />}
              {a.geo_score && <ScoreCard name="GEO" score={a.geo_score} />}
            </div>
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
