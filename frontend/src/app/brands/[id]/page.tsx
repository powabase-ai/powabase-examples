"use client";

import * as React from "react";
import { use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ExternalLink,
  FileText,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useBrands } from "@/lib/hooks/useBrands";
import {
  useBriefs,
  useGenerateBrief,
  useResearchRuns,
  useRunResearch,
  useSourceMarkdown,
} from "@/lib/hooks/useResearch";
import type { Brief, CompetitorTeardown, ResearchRun } from "@/lib/api";

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
  const router = useRouter();
  const { data: brands } = useBrands();
  const brand = brands?.find((b) => b.id === id);

  const runs = useResearchRuns(id);
  const briefs = useBriefs(id);
  const runResearch = useRunResearch(id);
  const generateBrief = useGenerateBrief(id);

  const [topic, setTopic] = React.useState("");
  const [depth, setDepth] = React.useState("standard");
  const [selectedRun, setSelectedRun] = React.useState<string | null>(null);
  const [sourceForMd, setSourceForMd] = React.useState<string | null>(null);

  const briefByRun = React.useMemo(() => {
    const m = new Map<string, Brief>();
    briefs.data?.forEach((b) => b.research_run_id && m.set(b.research_run_id, b));
    return m;
  }, [briefs.data]);

  async function onRunResearch(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) return;
    try {
      const run = await runResearch.mutateAsync({ topic: topic.trim(), depth });
      setSelectedRun(run.id);
      setTopic("");
      toast.success("Research started — watch the progress");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start research");
    }
  }

  async function onGenerateBrief(runId: string) {
    try {
      await generateBrief.mutateAsync(runId);
      toast.success("Brief generated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Brief failed");
    }
  }

  const selected = runs.data?.find((r) => r.id === selectedRun);
  const selectedBrief = selectedRun ? briefByRun.get(selectedRun) : undefined;

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-muted-foreground hover:text-foreground">
              <ArrowLeft className="size-4" />
            </Link>
            <span className="font-display text-lg font-bold tracking-tight">
              Rank<span className="text-[rgb(var(--accent-gold))]">Forge</span>
            </span>
          </div>
          <select
            value={id}
            onChange={(e) => router.push(`/brands/${e.target.value}`)}
            className="h-9 rounded-md border border-input bg-card px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {brands?.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-6 py-8 lg:grid-cols-[380px_1fr]">
        <section className="flex flex-col gap-4">
          <div>
            <h1 className="font-display text-2xl font-bold">{brand?.name ?? "Brand"}</h1>
            {brand?.niche && (
              <p className="mt-1 text-sm text-muted-foreground">{brand.niche}</p>
            )}
          </div>

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
              <RunDetail run={selected} onViewSource={setSourceForMd} />

              {selected.status === "failed" && (
                <Card>
                  <CardContent className="py-4 text-sm text-destructive">
                    Research failed: {selected.error}
                  </CardContent>
                </Card>
              )}

              {selected.status === "done" &&
                (selectedBrief ? (
                  <BriefView brief={selectedBrief} />
                ) : (
                  <Card>
                    <CardContent className="flex items-center justify-between py-6">
                      <p className="text-sm text-muted-foreground">No brief yet.</p>
                      <Button
                        variant="gold"
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
      </main>

      <SourceDialog sourceId={sourceForMd} onClose={() => setSourceForMd(null)} />
    </div>
  );
}

function RunDetail({
  run,
  onViewSource,
}: {
  run: ResearchRun;
  onViewSource: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-display text-lg">{run.topic}</CardTitle>
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <StatusBadge run={run} /> · intent: {run.intent ?? "—"} ·{" "}
          {run.serp?.results?.length ?? 0} results · {run.competitors.length} sources
        </p>
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
        <div className="max-h-[70vh] overflow-y-auto rounded-md border border-border bg-[rgb(var(--bg-surface-200))] p-3">
          {md.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {md.error && (
            <p className="text-sm text-destructive">{(md.error as Error).message}</p>
          )}
          {md.data && (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
              {md.data.markdown}
            </pre>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function BriefView({ brief }: { brief: Brief }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-display text-lg">
          <FileText className="size-4" /> Content brief
        </CardTitle>
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
