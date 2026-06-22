"use client";

import { use, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, Pencil, RefreshCw, Save, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ArticleEditor } from "@/components/ArticleEditor";
import { Markdown } from "@/components/Markdown";
import {
  useArticle,
  useOptimizeArticle,
  useUpdateArticle,
} from "@/lib/hooks/useArticles";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { cn } from "@/lib/utils";
import type { Score, ScoreSignal } from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  grounding: "Grounding in research sources…",
  outlining: "Outlining…",
  drafting: "Drafting sections…",
  optimizing: "Optimizing & scoring…",
  queued: "Queued…",
};

function scoreColor(s?: Score | null) {
  if (!s) return "var(--muted-ink)";
  return s.met ? "var(--success)" : "var(--ember)";
}

function SignalRow({ s }: { s: ScoreSignal }) {
  return (
    <div className="flex items-start gap-3 border-t border-border py-2.5 first:border-0">
      <span className="w-7 shrink-0 font-data text-sm">{s.score}</span>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {s.label}
          {s.method === "llm" && (
            <Sparkles className="size-3 text-[rgb(var(--ember))]" />
          )}
        </div>
        <div className="text-xs text-muted-foreground">{s.explanation}</div>
        {s.fixes.map((f, i) => (
          <div key={i} className="mt-0.5 text-xs text-[rgb(var(--ember-deep))]">
            → {f}
          </div>
        ))}
      </div>
    </div>
  );
}

function EvalBody({ score }: { score: Score }) {
  const color = scoreColor(score);
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {score.met ? "Meets target" : "Below target"}
        </span>
        <span className="font-data text-lg font-semibold" style={{ color: `rgb(${color})` }}>
          {score.total}
          <span className="text-sm text-muted-foreground">/{score.target}</span>
        </span>
      </div>
      <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div
          className="h-full rounded-full"
          style={{ width: `${score.total}%`, background: `rgb(${color})` }}
        />
      </div>
      {score.signals.map((s) => (
        <SignalRow key={s.key} s={s} />
      ))}
    </div>
  );
}

export default function ArticleView({
  params,
}: {
  params: Promise<{ id: string; articleId: string }>;
}) {
  const { id, articleId } = use(params);
  const { data: a, isLoading } = useArticle(articleId);
  const optimize = useOptimizeArticle(articleId);
  const update = useUpdateArticle(articleId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"SEO" | "GEO">("SEO");

  const generating = a && !["done", "failed"].includes(a.generation_status);
  const schemaTypes =
    ((a?.json_ld?.["@graph"] as Array<{ "@type"?: string }>) ?? [])
      .map((g) => g["@type"])
      .filter(Boolean) as string[];
  const current = tab === "SEO" ? a?.seo_score : a?.geo_score;

  async function save() {
    try {
      await update.mutateAsync({ content_md: draft });
      setEditing(false);
      toast.success("Saved — re-optimizing");
      optimize.mutate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    }
  }

  return (
    <ResizablePanelGroup
      direction="horizontal"
      autoSaveId="rankforge:article"
      className="h-full"
    >
      {/* Secondary sidebar — SEO / GEO evals */}
      <ResizablePanel defaultSize={26} minSize={16} maxSize={45}>
        <aside className="flex h-full w-full flex-col bg-card">
        <div className="flex border-b border-border">
          {(["SEO", "GEO"] as const).map((t) => {
            const sc = t === "SEO" ? a?.seo_score : a?.geo_score;
            const active = tab === t;
            return (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 border-b-2 px-4 py-3 text-sm font-semibold transition-colors",
                  active
                    ? "border-[rgb(var(--ember))] text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                {t}
                {sc && (
                  <span className="font-data text-xs" style={{ color: `rgb(${scoreColor(sc)})` }}>
                    {sc.total}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {current ? (
            <EvalBody score={current} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {generating ? "Scores appear once generation finishes." : "No scores yet."}
            </p>
          )}
          {a && a.generation_status === "done" && (
            <Button
              variant="outline"
              size="sm"
              className="mt-4 w-full"
              onClick={() => optimize.mutate()}
              disabled={optimize.isPending}
            >
              {optimize.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Re-optimize & score
            </Button>
          )}
        </div>
        </aside>
      </ResizablePanel>
      <ResizableHandle />

      {/* Content */}
      <ResizablePanel minSize={45} className="overflow-y-auto">
        <div className="mx-auto max-w-3xl px-8 py-8">
          <div className="mb-4 flex items-center justify-between">
            <Link
              href={`/brands/${id}/articles`}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-4" /> Articles
            </Link>
            {a && a.generation_status === "done" && !editing && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setDraft(a.content_md);
                  setEditing(true);
                }}
              >
                <Pencil /> Edit
              </Button>
            )}
            {editing && (
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
                  <X /> Cancel
                </Button>
                <Button variant="gold" size="sm" onClick={save} disabled={update.isPending}>
                  {update.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save
                </Button>
              </div>
            )}
          </div>

          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

          {a && (
            <>
              <h1 className="font-display text-3xl font-bold leading-tight">{a.title}</h1>
              {a.meta_description && (
                <p className="mt-2 text-sm text-muted-foreground">{a.meta_description}</p>
              )}
              {schemaTypes.length > 0 && (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  schema.org: {schemaTypes.join(" · ")}
                </p>
              )}
              {a.json_ld && (
                <script
                  type="application/ld+json"
                  dangerouslySetInnerHTML={{ __html: JSON.stringify(a.json_ld) }}
                />
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

              {editing ? (
                <div className="mt-8">
                  <ArticleEditor value={a.content_md} onChange={setDraft} />
                </div>
              ) : (
                a.content_md && (
                  <article className="mt-8">
                    <Markdown>{a.content_md}</Markdown>
                  </article>
                )
              )}
            </>
          )}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
