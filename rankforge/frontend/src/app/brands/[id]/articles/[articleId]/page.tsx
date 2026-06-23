"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  History,
  Loader2,
  MessageSquare,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Share2,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ArticleEditor } from "@/components/ArticleEditor";
import { CommentsPanel } from "@/components/CommentsPanel";
import { Markdown } from "@/components/Markdown";
import { PublishDialog } from "@/components/PublishDialog";
import { useAuth } from "@/lib/auth/AuthProvider";
import {
  useArticle,
  useArticleVersions,
  useOptimizeArticle,
  useRefineArticle,
  useRestoreVersion,
  useUpdateArticle,
} from "@/lib/hooks/useArticles";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { cn } from "@/lib/utils";
import { ARTICLE_STATUSES, canApprove } from "@/lib/api";
import type { Article, GroundingReport, Score, ScoreSignal } from "@/lib/api";

const GATED_STATUSES = new Set(["approved", "published"]);

const PHASE_LABEL: Record<string, string> = {
  grounding: "Grounding in research sources…",
  outlining: "Outlining…",
  drafting: "Drafting sections…",
  optimizing: "Optimizing & scoring…",
  refining: "Refining to hit SEO/GEO targets…",
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

function GroundingBody({ report }: { report: GroundingReport }) {
  const s = report.grounding_score;
  const color = s == null ? "var(--muted-ink)" : s >= 80 ? "var(--success)" : "var(--ember)";
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {report.supported ?? 0}/{report.claims_checked ?? 0} claims supported
        </span>
        <span className="font-data text-lg font-semibold" style={{ color: `rgb(${color})` }}>
          {s ?? "—"}
          {s != null && <span className="text-sm text-muted-foreground">/100</span>}
        </span>
      </div>
      {s != null && (
        <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-secondary">
          <div
            className="h-full rounded-full"
            style={{ width: `${s}%`, background: `rgb(${color})` }}
          />
        </div>
      )}
      {report.error && (
        <p className="mb-3 text-xs text-muted-foreground">{report.error}</p>
      )}
      <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
        Flagged claims
      </p>
      {report.flagged && report.flagged.length > 0 ? (
        report.flagged.map((f, i) => (
          <div key={i} className="border-t border-border py-2.5 first:border-0">
            <div className="text-sm font-medium">{f.claim}</div>
            <div className="text-xs text-muted-foreground">{f.issue}</div>
            {f.suggestion && (
              <div className="mt-0.5 text-xs text-[rgb(var(--ember-deep))]">
                → {f.suggestion}
              </div>
            )}
          </div>
        ))
      ) : (
        <p className="text-sm text-muted-foreground">No unsupported claims flagged.</p>
      )}
    </div>
  );
}

const REFINE_STEPS = ["revising", "fact-checking", "optimizing", "scoring"];

// One monotonic 0→1 estimate across the whole pipeline, so the bar always moves
// forward (drafting and refining have real sub-counts; other phases are coarse).
function overallFraction(a: Article): number {
  const p = a.progress ?? {};
  switch (a.generation_status) {
    case "queued":
      return 0.03;
    case "grounding":
      return 0.1;
    case "outlining":
      return 0.22;
    case "drafting":
      return 0.25 + 0.4 * (p.total ? (p.done ?? 0) / p.total : 0);
    case "optimizing":
      return 0.7;
    case "refining": {
      const total = p.total ?? 2;
      const it = (p.iteration ?? 1) - 1;
      const si = p.step ? Math.max(0, REFINE_STEPS.indexOf(p.step)) : 0;
      const passFrac = (it + si / REFINE_STEPS.length) / total;
      return 0.75 + 0.22 * Math.min(1, passFrac);
    }
    default:
      return 0.5;
  }
}

function LiveScore({ label, score }: { label: string; score?: Score | null }) {
  return (
    <span>
      {label}{" "}
      <b
        className="font-data"
        style={{ color: score ? `rgb(${scoreColor(score)})` : undefined }}
      >
        {score?.total ?? "—"}
      </b>
      {score ? (
        <span className="text-muted-foreground">/{score.target}</span>
      ) : null}
    </span>
  );
}

function GenerationProgress({ a }: { a: Article }) {
  const p = a.progress ?? {};
  // Clamp — overallFraction reads runtime JSON that could be malformed.
  const pct = Math.round(Math.min(1, Math.max(0, overallFraction(a))) * 100);
  let detail = "";
  if (a.generation_status === "drafting" && p.total)
    detail = `section ${p.done ?? 0}/${p.total}`;
  if (a.generation_status === "refining")
    detail = `pass ${p.iteration ?? 1}/${p.total ?? 2}${
      p.step ? ` · ${p.step}` : ""
    }`;

  return (
    <Card className="mt-6">
      <CardContent className="py-5">
        <div className="mb-2.5 flex items-center gap-2.5 text-sm">
          <Loader2 className="size-4 animate-spin text-[rgb(var(--ember-bright))]" />
          <span className="font-medium">
            {PHASE_LABEL[a.generation_status] ?? a.generation_status}
          </span>
          {detail && <span className="text-muted-foreground">· {detail}</span>}
          <span className="ml-auto font-data text-xs text-muted-foreground">
            {pct}%
          </span>
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-secondary"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-[rgb(var(--ember))] transition-[width] duration-500 ease-out"
            style={{ width: `${pct}%` }}
          />
        </div>
        {/* During refinement the scores update each pass — let the user watch them move. */}
        {a.generation_status === "refining" && (
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
            <LiveScore label="SEO" score={a.seo_score} />
            <LiveScore label="GEO" score={a.geo_score} />
            <span>
              Grounding{" "}
              <b className="font-data text-foreground">
                {a.grounding_report?.grounding_score ?? "—"}
              </b>
            </span>
          </div>
        )}
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
  const optimize = useOptimizeArticle(articleId);
  const refine = useRefineArticle(articleId);
  const update = useUpdateArticle(articleId);
  const versions = useArticleVersions(articleId);
  const restore = useRestoreVersion(articleId);
  const { profile } = useAuth();
  const mayApprove = canApprove(profile?.role);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"SEO" | "GEO" | "Grounding" | "Comments">("SEO");
  const [showHistory, setShowHistory] = useState(false);
  const [showPublish, setShowPublish] = useState(false);

  const generating = a && !["done", "failed"].includes(a.generation_status);
  const dirty = !!a && editing && draft !== a.content_md;

  // The route component is reused across article ids — reset view/edit state so an
  // open editor (and its stale draft) never carries over to a different article.
  useEffect(() => {
    setEditing(false);
    setDraft("");
    setTab("SEO");
    setShowHistory(false);
    setShowPublish(false);
  }, [articleId]);

  // Warn before leaving with unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
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

  function cancelEdit() {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    setEditing(false);
  }

  function changeStatus(next: string) {
    update.mutate(
      { status: next },
      {
        onSuccess: () => toast.success(`Marked ${next.replace(/_/g, " ")}`),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Status change failed"),
      }
    );
  }

  async function doRestore(versionId: string) {
    try {
      await restore.mutateAsync(versionId);
      setShowHistory(false);
      toast.success("Restored — re-optimizing");
      optimize.mutate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Restore failed");
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
          {(["SEO", "GEO", "Grounding", "Comments"] as const).map((t) => {
            const sc = t === "SEO" ? a?.seo_score : t === "GEO" ? a?.geo_score : null;
            const g = t === "Grounding" ? a?.grounding_report?.grounding_score : null;
            const badge = sc ? sc.total : g ?? null;
            const color = sc
              ? scoreColor(sc)
              : g != null
                ? g >= 80
                  ? "var(--success)"
                  : "var(--ember)"
                : "var(--muted-ink)";
            return (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 border-b-2 px-2 py-3 text-xs font-semibold transition-colors",
                  tab === t
                    ? "border-[rgb(var(--ember))] text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
                title={t}
              >
                {t === "Comments" ? <MessageSquare className="size-3.5" /> : t}
                {badge != null && (
                  <span className="font-data" style={{ color: `rgb(${color})` }}>
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {tab === "Comments" ? (
          <div className="min-h-0 flex-1 p-4">
            <CommentsPanel articleId={articleId} />
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {tab === "Grounding" ? (
              a?.grounding_report ? (
                <GroundingBody report={a.grounding_report} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  {generating ? "Fact-check runs after generation." : "No fact-check yet."}
                </p>
              )
            ) : current ? (
              <EvalBody score={current} />
            ) : (
              <p className="text-sm text-muted-foreground">
                {generating ? "Scores appear once generation finishes." : "No scores yet."}
              </p>
            )}
            {a && a.generation_status === "done" && (
              <div className="mt-4 space-y-2">
                <Button
                  variant="gold"
                  size="sm"
                  className="w-full"
                  onClick={() =>
                    refine.mutate(undefined, {
                      onSuccess: () =>
                        toast.success("Refining against SEO/GEO/Grounding…"),
                      onError: (e) =>
                        toast.error(
                          e instanceof Error ? e.message : "Refine failed"
                        ),
                    })
                  }
                  disabled={refine.isPending}
                >
                  {refine.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Sparkles />
                  )}
                  Auto-refine to targets
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={() => optimize.mutate()}
                  disabled={optimize.isPending}
                >
                  {optimize.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <RefreshCw />
                  )}
                  Re-optimize & score
                </Button>
              </div>
            )}
          </div>
        )}
        </aside>
      </ResizablePanel>
      <ResizableHandle />

      {/* Content */}
      <ResizablePanel minSize={45}>
        <div className="h-full overflow-y-auto">
          <div className="mx-auto max-w-3xl px-8 py-8">
          <div className="mb-4 flex items-center justify-between">
            <Link
              href={`/brands/${id}/articles`}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-4" /> Articles
            </Link>
            {a && a.generation_status === "done" && !editing && (
              <div className="flex items-center gap-2">
                <select
                  value={a.status}
                  onChange={(e) => changeStatus(e.target.value)}
                  disabled={update.isPending}
                  className="h-8 rounded-md border border-input bg-card px-2 text-xs font-medium capitalize text-foreground outline-none focus:ring-1 focus:ring-[rgb(var(--ember))]"
                  aria-label="Article status"
                >
                  {ARTICLE_STATUSES.map((s) => (
                    <option
                      key={s}
                      value={s}
                      disabled={!mayApprove && GATED_STATUSES.has(s)}
                    >
                      {s.replace(/_/g, " ")}
                      {!mayApprove && GATED_STATUSES.has(s) ? " (editor)" : ""}
                    </option>
                  ))}
                </select>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowHistory(true)}
                >
                  <History /> History
                </Button>
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
                <Button
                  variant="gold"
                  size="sm"
                  onClick={() => setShowPublish(true)}
                >
                  <Share2 /> Publish
                </Button>
              </div>
            )}
            {editing && (
              <span className="text-xs text-muted-foreground">
                Editing — controls are in the toolbar below
              </span>
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
                  // Escape "<" so generated/scraped content can't break out of the
                  // <script> tag (e.g. a "</script>" substring) — XSS guard.
                  dangerouslySetInnerHTML={{
                    __html: JSON.stringify(a.json_ld).replace(/</g, "\\u003c"),
                  }}
                />
              )}

              {generating && <GenerationProgress a={a} />}

              {a.generation_status === "failed" && (
                <Card className="mt-6">
                  <CardContent className="py-5 text-sm text-destructive">
                    Generation failed: {a.generation_error}
                  </CardContent>
                </Card>
              )}

              {editing ? (
                <div className="mt-8">
                  <ArticleEditor
                    key={articleId}
                    value={a.content_md}
                    onChange={setDraft}
                    actions={
                      <>
                        {dirty && (
                          <span className="text-xs font-medium text-[rgb(var(--ember))]">
                            Unsaved
                          </span>
                        )}
                        <Button variant="outline" size="sm" onClick={cancelEdit}>
                          <X /> Cancel
                        </Button>
                        <Button
                          variant="gold"
                          size="sm"
                          onClick={save}
                          disabled={update.isPending || !dirty}
                        >
                          {update.isPending ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <Save />
                          )}{" "}
                          Save
                        </Button>
                      </>
                    }
                  />
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
        </div>
      </ResizablePanel>

      {a && (
        <PublishDialog
          open={showPublish}
          onOpenChange={setShowPublish}
          articleId={articleId}
          slug={a.slug}
          published={a.status === "published"}
        />
      )}

      <Dialog open={showHistory} onOpenChange={setShowHistory}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Version history</DialogTitle>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto">
            {versions.isLoading && (
              <p className="py-4 text-sm text-muted-foreground">Loading…</p>
            )}
            {versions.data?.length === 0 && (
              <p className="py-4 text-sm text-muted-foreground">
                No prior versions yet. A snapshot is saved each time you edit and
                save.
              </p>
            )}
            {versions.data?.map((v) => (
              <div
                key={v.id}
                className="flex items-center justify-between gap-3 border-t border-border py-3 first:border-0"
              >
                <div className="min-w-0 text-sm">
                  <div className="font-medium">
                    {new Date(v.created_at).toLocaleString()}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    <span className="font-data">{v.word_count ?? 0}</span> words
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => doRestore(v.id)}
                  disabled={restore.isPending}
                >
                  {restore.isPending && restore.variables === v.id ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <RotateCcw />
                  )}
                  Restore
                </Button>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </ResizablePanelGroup>
  );
}
