"use client";

import * as React from "react";
import { use } from "react";
import { useRouter } from "next/navigation";
import {
  ExternalLink,
  FileText,
  Loader2,
  Pencil,
  PenLine,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/components/Markdown";
import { Page, PageHeader } from "@/components/layout/PageHeader";
import { useGenerateArticle } from "@/lib/hooks/useArticles";
import { useBrands } from "@/lib/hooks/useBrands";
import {
  useBriefs,
  useDeleteResearchRun,
  useGenerateBrief,
  useResearchRuns,
  useRunResearch,
  useSourceMarkdown,
  useTemplates,
  useUpdateBrief,
} from "@/lib/hooks/useResearch";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove, TERMINAL_RESEARCH } from "@/lib/api";
import type { Brief, BriefUpdate, CompetitorTeardown, ResearchRun } from "@/lib/api";

function StatusBadge({ run }: { run: ResearchRun }) {
  if (run.status === "done")
    return (
      <span className="inline-flex items-center gap-1 text-[rgb(var(--success))]">
        ● done
      </span>
    );
  if (run.status === "failed")
    return <span className="text-destructive">● failed</span>;
  const label =
    run.status === "scraping"
      ? `scraping ${run.progress?.done ?? 0}/${run.progress?.total ?? "?"}`
      : run.status;
  return (
    <span className="inline-flex items-center gap-1 text-[rgb(var(--accent-gold-hover))]">
      <Loader2 className="size-3 animate-spin" /> {label}
    </span>
  );
}

export default function BrandWorkspace({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data: brands } = useBrands();
  const brand = brands?.find((b) => b.id === id);

  const runs = useResearchRuns(id);
  const briefs = useBriefs(id);
  const runResearch = useRunResearch(id);
  const generateBrief = useGenerateBrief(id);
  const generateArticle = useGenerateArticle(id);
  const { data: templates } = useTemplates();
  const router = useRouter();

  const [topic, setTopic] = React.useState("");
  const [depth, setDepth] = React.useState("standard");
  const [evaluateSources, setEvaluateSources] = React.useState(true);
  const [selectedRun, setSelectedRun] = React.useState<string | null>(null);
  const [sourceForMd, setSourceForMd] = React.useState<string | null>(null);
  const [articleType, setArticleType] = React.useState("general");

  const briefByRun = React.useMemo(() => {
    const m = new Map<string, Brief>();
    briefs.data?.forEach((b) => b.research_run_id && m.set(b.research_run_id, b));
    return m;
  }, [briefs.data]);

  async function onRunResearch(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) return;
    try {
      const run = await runResearch.mutateAsync({
        topic: topic.trim(),
        depth,
        evaluate_sources: evaluateSources,
      });
      setSelectedRun(run.id);
      setTopic("");
      toast.success("Research started — watch the progress");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start research");
    }
  }

  async function onGenerateBrief(runId: string) {
    try {
      await generateBrief.mutateAsync({ researchRunId: runId, articleType });
      toast.success("Brief generated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Brief failed");
    }
  }

  async function onGenerateDraft(briefId: string) {
    try {
      const a = await generateArticle.mutateAsync(briefId);
      toast.success("Draft started");
      router.push(`/brands/${id}/articles/${a.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start generation");
    }
  }

  const selected = runs.data?.find((r) => r.id === selectedRun);
  const selectedBrief = selectedRun ? briefByRun.get(selectedRun) : undefined;

  return (
    <Page>
      <PageHeader icon={Search} title="Research" meta={brand?.niche ?? undefined} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-6xl gap-6 px-6 py-8 lg:grid-cols-[380px_1fr]">
          <section className="flex flex-col gap-4">
            <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Search className="size-4" /> New research
              </CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={onRunResearch} className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="topic">Topic</Label>
                  <Input
                    id="topic"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="best backend as a service"
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="depth">Depth</Label>
                  <select
                    id="depth"
                    value={depth}
                    onChange={(e) => setDepth(e.target.value)}
                    className="h-9 rounded-md border border-input bg-card px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <option value="quick">Quick (5 SERP, 3 scraped)</option>
                    <option value="standard">Standard (10, 5)</option>
                    <option value="deep">Deep (20, 10)</option>
                  </select>
                </div>
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={evaluateSources}
                    onChange={(e) => setEvaluateSources(e.target.checked)}
                    className="mt-0.5 size-4 shrink-0 rounded border-input accent-[rgb(var(--gold))]"
                  />
                  <span>
                    Evaluate &amp; prune source quality
                    <span className="block text-xs text-muted-foreground">
                      Scores each source for authority, drops weak SEO blogs, and
                      backfills stronger ones. Uses extra credits.
                    </span>
                  </span>
                </label>
                <Button type="submit" variant="gold" disabled={runResearch.isPending || !topic.trim()}>
                  <Search /> Run research
                </Button>
                <p className="text-xs text-muted-foreground">
                  Runs in the background — progress shows on the run below; you can keep
                  working.
                </p>
              </form>
            </CardContent>
          </Card>

          <div className="flex flex-col gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Research runs
            </h2>
            {runs.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
            {runs.data?.length === 0 && (
              <p className="text-sm text-muted-foreground">No runs yet.</p>
            )}
            {runs.data?.map((r) => (
              <button
                key={r.id}
                onClick={() => setSelectedRun(r.id)}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  r.id === selectedRun
                    ? "border-[rgb(var(--accent-gold))] bg-[rgb(var(--accent-gold-muted))]"
                    : "border-border bg-card hover:bg-secondary"
                }`}
              >
                <div className="line-clamp-1 text-sm font-medium">{r.topic}</div>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <StatusBadge run={r} /> ·
                  <span>{r.serp?.results?.length ?? 0} SERP</span> ·
                  <span>{r.competitors.length} sources</span>
                  {briefByRun.has(r.id) && (
                    <span className="inline-flex items-center gap-1 text-[rgb(var(--accent-gold-hover))]">
                      <FileText className="size-3" /> brief
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-4">
          {!selected && (
            <Card className="border-dashed">
              <CardContent className="py-16 text-center text-sm text-muted-foreground">
                Run research or pick a run to see its SERP, scraped sources, and brief.
              </CardContent>
            </Card>
          )}

          {selected && (
            <>
              <RunDetail
                run={selected}
                onViewSource={setSourceForMd}
                brandId={id}
                canEdit={canEdit}
                onDeleted={() => setSelectedRun(null)}
              />

              {selected.status === "failed" && (
                <Card>
                  <CardContent className="py-4 text-sm text-destructive">
                    Research failed: {selected.error}
                  </CardContent>
                </Card>
              )}

              {selected.status === "done" &&
                (selectedBrief ? (
                  <BriefView
                    brief={selectedBrief}
                    businessId={id}
                    canEdit={canEdit}
                    onGenerate={() => onGenerateDraft(selectedBrief.id)}
                    generating={generateArticle.isPending}
                  />
                ) : (
                  <Card>
                    <CardContent className="flex flex-col gap-3 py-5">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-sm text-muted-foreground">
                          No brief yet — pick an article type:
                        </p>
                        <select
                          value={articleType}
                          onChange={(e) => setArticleType(e.target.value)}
                          className="h-9 rounded-md border border-input bg-card px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {templates?.map((t) => (
                            <option key={t.type} value={t.type}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <Button
                        variant="gold"
                        className="self-end"
                        disabled={generateBrief.isPending}
                        onClick={() => onGenerateBrief(selected.id)}
                      >
                        {generateBrief.isPending ? (
                          <>
                            <Loader2 className="animate-spin" /> Generating…
                          </>
                        ) : (
                          <>
                            <Sparkles /> Generate brief
                          </>
                        )}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
            </>
          )}
        </section>
        </div>
      </div>

      <SourceDialog sourceId={sourceForMd} onClose={() => setSourceForMd(null)} />
    </Page>
  );
}

function RunDetail({
  run,
  onViewSource,
  brandId,
  canEdit,
  onDeleted,
}: {
  run: ResearchRun;
  onViewSource: (id: string) => void;
  brandId: string;
  canEdit: boolean;
  onDeleted: () => void;
}) {
  const del = useDeleteResearchRun(brandId);

  function onDelete() {
    if (
      !window.confirm(
        `Delete the research run “${run.topic}”? Its scraped sources are removed ` +
          "from Powabase too (unless another run or your brand materials still use them)."
      )
    )
      return;
    del.mutate(run.id, {
      onSuccess: () => {
        toast.success("Research run deleted");
        onDeleted();
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
    });
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="font-display text-lg">{run.topic}</CardTitle>
            <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
              <StatusBadge run={run} /> · intent: {run.intent ?? "—"} ·{" "}
              {run.serp?.results?.length ?? 0} results · {run.competitors.length}{" "}
              sources
            </p>
          </div>
          {canEdit && TERMINAL_RESEARCH.includes(run.status) && (
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0 text-muted-foreground hover:text-destructive"
              onClick={onDelete}
              disabled={del.isPending}
              title="Delete this research run"
            >
              {del.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Trash2 />
              )}
              Delete
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Top SERP
          </p>
          <ul className="grid gap-1">
            {(run.serp?.results ?? []).slice(0, 6).map((r, i) => (
              <li key={i} className="line-clamp-1">
                <a href={r.url ?? "#"} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                  {r.title ?? r.url}
                </a>
              </li>
            ))}
          </ul>
        </div>

        {run.competitors.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Scraped sources (Powabase)
            </p>
            <div className="grid gap-2">
              {run.competitors.map((c: CompetitorTeardown, i) => (
                <div key={i} className="rounded-md border border-border p-2.5">
                  <div className="line-clamp-1 text-sm font-medium">{c.title}</div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{c.word_count ?? "—"} words</span>
                    <span>{c.headings.length} headings</span>
                    {c.url && (
                      <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                        <ExternalLink className="size-3" /> page
                      </a>
                    )}
                    {c.source_id && (
                      <button
                        onClick={() => onViewSource(c.source_id as string)}
                        className="ml-auto inline-flex items-center gap-1 text-[rgb(var(--accent-gold-hover))] hover:underline"
                      >
                        <FileText className="size-3" /> view scraped text
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {(run.serp?.paa?.length ?? 0) > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              People also ask
            </p>
            <ul className="grid gap-1 text-muted-foreground">
              {run.serp.paa!.slice(0, 8).map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SourceDialog({
  sourceId,
  onClose,
}: {
  sourceId: string | null;
  onClose: () => void;
}) {
  const md = useSourceMarkdown(sourceId);
  return (
    <Dialog open={!!sourceId} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] w-[90vw] max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-display">Scraped page (markdown)</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] overflow-y-auto rounded-md border border-border bg-card p-4">
          {md.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {md.error && (
            <p className="text-sm text-destructive">{(md.error as Error).message}</p>
          )}
          {md.data && <Markdown>{md.data.markdown}</Markdown>}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function BriefView({
  brief,
  businessId,
  canEdit,
  onGenerate,
  generating,
}: {
  brief: Brief;
  businessId: string;
  canEdit: boolean;
  onGenerate: () => void;
  generating: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 font-display text-lg">
            <FileText className="size-4" /> Content brief
            {brief.article_type && brief.article_type !== "general" && (
              <span className="rounded bg-secondary px-1.5 py-0.5 text-xs font-normal capitalize text-muted-foreground">
                {brief.article_type.replace(/_/g, " ")}
              </span>
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            {canEdit && (
              <EditBriefDialog brief={brief} businessId={businessId} />
            )}
            <Button variant="gold" size="sm" onClick={onGenerate} disabled={generating}>
              {generating ? (
                <>
                  <Loader2 className="animate-spin" /> Starting…
                </>
              ) : (
                <>
                  <PenLine /> Generate draft
                </>
              )}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <Field label="Suggested title">{brief.suggested_title}</Field>
        <Field label="Meta description">{brief.suggested_meta}</Field>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Primary keyword">{brief.primary_keyword}</Field>
          <Field label="Target word count">{brief.target_word_count}</Field>
        </div>
        <Chips label="Secondary keywords" items={brief.secondary_keywords} />
        <Chips label="Must-cover entities" items={brief.entities} />
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Heading outline
          </p>
          <ul className="grid gap-0.5">
            {brief.headings.map((h, i) => (
              <li key={i} className={h.startsWith("H3") ? "pl-4 text-muted-foreground" : "font-medium"}>
                {h}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Questions to answer
          </p>
          <ul className="grid list-disc gap-0.5 pl-5 text-muted-foreground">
            {brief.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

const linesToArray = (s: string): string[] =>
  s.split("\n").map((t) => t.trim()).filter(Boolean);

// The brief stores headings as "H2:"/"H3:"-prefixed lines; the editor shows them as
// Markdown (##/###) since that reads as the heading hierarchy it represents.
const headingsToMarkdown = (headings: string[]): string =>
  headings
    .map((h) => {
      const m = h.match(/^H([23]):\s*(.*)$/i);
      return m ? `${m[1] === "3" ? "###" : "##"} ${m[2]}`.trim() : h;
    })
    .join("\n");

const markdownToHeadings = (text: string): string[] =>
  linesToArray(text).map((line) => {
    const m = line.match(/^(#{1,6})\s+(.*)$/);
    return m ? `H${m[1].length >= 3 ? 3 : 2}: ${m[2]}` : line;
  });

/** Edit a generated brief before drafting: the writer-facing contract every downstream
 *  agent obeys. Self-contained (owns open state + its trigger), so opening re-seeds the
 *  fields from the current (AI-generated) brief. List fields are edited one-per-line;
 *  the heading outline is Markdown (##/###), converted to the brief's H2:/H3: on save. */
function EditBriefDialog({
  brief,
  businessId,
}: {
  brief: Brief;
  businessId: string;
}) {
  const update = useUpdateBrief(businessId);
  const [open, setOpen] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [meta, setMeta] = React.useState("");
  const [primaryKw, setPrimaryKw] = React.useState("");
  const [wordCount, setWordCount] = React.useState("");
  const [secondary, setSecondary] = React.useState("");
  const [entities, setEntities] = React.useState("");
  const [headings, setHeadings] = React.useState("");
  const [questions, setQuestions] = React.useState("");

  function onOpenChange(v: boolean) {
    // The trigger drives onOpenChange, so seed on open — every open reflects the latest
    // brief and discards any prior canceled edit.
    if (v) {
      setTitle(brief.suggested_title ?? "");
      setMeta(brief.suggested_meta ?? "");
      setPrimaryKw(brief.primary_keyword ?? "");
      setWordCount(
        brief.target_word_count != null ? String(brief.target_word_count) : ""
      );
      setSecondary(brief.secondary_keywords.join("\n"));
      setEntities(brief.entities.join("\n"));
      setHeadings(headingsToMarkdown(brief.headings));
      setQuestions(brief.questions.join("\n"));
    }
    setOpen(v);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const wc = wordCount.trim();
    const wcNum = wc === "" ? null : Number(wc);
    if (wcNum !== null && (!Number.isInteger(wcNum) || wcNum < 0)) {
      toast.error("Target word count must be a whole number of 0 or more");
      return;
    }
    const data: BriefUpdate = {
      suggested_title: title.trim() || null,
      suggested_meta: meta.trim() || null,
      primary_keyword: primaryKw.trim() || null,
      target_word_count: wcNum,
      secondary_keywords: linesToArray(secondary),
      entities: linesToArray(entities),
      headings: markdownToHeadings(headings),
      questions: linesToArray(questions),
    };
    update.mutate(
      { id: brief.id, data },
      {
        onSuccess: () => {
          toast.success("Brief updated");
          setOpen(false);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "Update failed"),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" title="Edit this brief before drafting">
          <Pencil /> Edit
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="font-display">Edit content brief</DialogTitle>
          <DialogDescription>
            The brief is the contract the writer follows. List fields are one entry per
            line; the heading outline uses Markdown (<code>##</code> section,{" "}
            <code>###</code> subsection).
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="brief-title">Suggested title</Label>
            <Input
              id="brief-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="brief-meta">Meta description</Label>
            <Textarea
              id="brief-meta"
              value={meta}
              onChange={(e) => setMeta(e.target.value)}
              rows={2}
              maxLength={320}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="brief-kw">Primary keyword</Label>
              <Input
                id="brief-kw"
                value={primaryKw}
                onChange={(e) => setPrimaryKw(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="brief-wc">Target word count</Label>
              <Input
                id="brief-wc"
                type="number"
                min={0}
                value={wordCount}
                onChange={(e) => setWordCount(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="brief-secondary">Secondary keywords</Label>
            <Textarea
              id="brief-secondary"
              value={secondary}
              onChange={(e) => setSecondary(e.target.value)}
              rows={3}
              placeholder="One keyword per line"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="brief-entities">Must-cover entities</Label>
            <Textarea
              id="brief-entities"
              value={entities}
              onChange={(e) => setEntities(e.target.value)}
              rows={3}
              placeholder="One entity per line"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="brief-headings">Heading outline</Label>
            <Textarea
              id="brief-headings"
              value={headings}
              onChange={(e) => setHeadings(e.target.value)}
              rows={6}
              className="font-mono text-xs"
              placeholder={"## Section heading\n### Subsection heading"}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="brief-questions">Questions to answer</Label>
            <Textarea
              id="brief-questions"
              value={questions}
              onChange={(e) => setQuestions(e.target.value)}
              rows={4}
              placeholder="One question per line"
            />
          </div>
          <DialogFooter className="mt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" variant="gold" disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save brief"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-0.5">{children || "—"}</p>
    </div>
  );
}

function Chips({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <span key={i} className="rounded-md bg-secondary px-2 py-0.5 text-xs">
            {it}
          </span>
        ))}
      </div>
    </div>
  );
}
