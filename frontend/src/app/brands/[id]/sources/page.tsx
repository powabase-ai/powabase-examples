"use client";

import * as React from "react";
import { use } from "react";
import { ExternalLink, FileText, Layers } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Markdown } from "@/components/Markdown";
import { useBrandSources, useSourceMarkdown } from "@/lib/hooks/useResearch";
import type { BrandSource } from "@/lib/api";

export default function SourcesLibrary({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: sources, isLoading } = useBrandSources(id);
  const [selected, setSelected] = React.useState<BrandSource | null>(null);
  const md = useSourceMarkdown(selected?.source_id ?? null);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-card px-6">
        <Layers className="size-4 text-muted-foreground" />
        <h1 className="font-display text-lg font-bold">Sources</h1>
        <span className="text-sm text-muted-foreground">
          {sources?.length ? `· ${sources.length} scraped pages` : ""}
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[360px_1fr]">
        {/* List */}
        <div className="min-h-0 overflow-y-auto border-r border-border">
          {isLoading && (
            <p className="p-6 text-sm text-muted-foreground">Loading…</p>
          )}
          {sources?.length === 0 && (
            <p className="p-6 text-sm text-muted-foreground">
              No sources yet. Run research in the Research tab to scrape competitor
              pages — they&apos;ll appear here.
            </p>
          )}
          <ul>
            {sources?.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => setSelected(s)}
                  className={`w-full border-b border-border px-4 py-3 text-left transition-colors ${
                    selected?.id === s.id
                      ? "bg-[rgb(var(--accent-gold-muted))]"
                      : "hover:bg-secondary"
                  }`}
                >
                  <div className="line-clamp-1 text-sm font-medium">
                    {s.title || s.url}
                  </div>
                  <div className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                    {s.url}
                  </div>
                  <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded bg-secondary px-1.5 py-0.5">
                      <span className="font-data">{s.word_count ?? "—"}</span> words
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <FileText className="size-3" /> {s.run_topic}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* Detail */}
        <div className="min-h-0 overflow-y-auto">
          {!selected && (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
              Select a source to read its scraped content.
            </div>
          )}
          {selected && (
            <div className="mx-auto max-w-3xl px-8 py-6">
              <div className="mb-4">
                <h2 className="font-display text-xl font-bold">{selected.title}</h2>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  {selected.url && (
                    <a
                      href={selected.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 hover:underline"
                    >
                      <ExternalLink className="size-3" /> {selected.url}
                    </a>
                  )}
                  <span>from research: “{selected.run_topic}”</span>
                  <span>{selected.word_count ?? "—"} words</span>
                </div>
              </div>
              <Card>
                <CardContent className="py-5">
                  {md.isLoading && (
                    <p className="text-sm text-muted-foreground">Loading content…</p>
                  )}
                  {md.error && (
                    <p className="text-sm text-destructive">
                      {(md.error as Error).message}
                    </p>
                  )}
                  {md.data && <Markdown>{md.data.markdown}</Markdown>}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
