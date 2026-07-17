"use client";

import * as React from "react";
import { use } from "react";
import { ExternalLink, FileText, Layers, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { SourceContentViewer } from "@/components/SourceContentViewer";
import { Page, PageHeader } from "@/components/layout/PageHeader";
import {
  useBrandSources,
  useDeleteBrandSources,
} from "@/lib/hooks/useResearch";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove, type BrandSource } from "@/lib/api";

/** Authority/trust score (0-100) from the research source-quality judge. Colored by
 *  tier; the judge's one-line reason is the tooltip. Nothing renders for an unscored
 *  source (evaluation was off, or it predates the feature). */
function TrustBadge({ score, reason }: { score?: number | null; reason?: string | null }) {
  if (score == null) return null;
  const tier =
    score >= 85
      ? { label: "high authority", cls: "bg-[rgb(var(--success))]/15 text-[rgb(var(--success))]" }
      : score >= 60
        ? { label: "solid", cls: "bg-[rgb(var(--gold))]/15 text-[rgb(var(--accent-gold-hover))]" }
        : score >= 40
          ? { label: "thin", cls: "bg-secondary text-muted-foreground" }
          : { label: "low authority", cls: "bg-destructive/15 text-destructive" };
  return (
    <span
      title={reason || undefined}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${tier.cls}`}
    >
      <span className="font-data font-medium">{score}</span> {tier.label}
    </span>
  );
}

export default function SourcesLibrary({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data: sources, isLoading } = useBrandSources(id);
  const del = useDeleteBrandSources(id);

  const [selected, setSelected] = React.useState<BrandSource | null>(null);
  const [checked, setChecked] = React.useState<Set<string>>(new Set());

  // Drop stale ids after the list changes (deletes, refetch).
  React.useEffect(() => {
    if (!sources) return;
    const live = new Set(sources.map((s) => s.id));
    setChecked((prev) => {
      const next = new Set([...prev].filter((x) => live.has(x)));
      return next.size === prev.size ? prev : next;
    });
    setSelected((prev) => (prev && live.has(prev.id) ? prev : null));
  }, [sources]);

  const allChecked = !!sources?.length && checked.size === sources.length;

  function toggle(rowId: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }
  function toggleAll() {
    setChecked(allChecked ? new Set() : new Set(sources?.map((s) => s.id) ?? []));
  }

  function onDelete() {
    const ids = [...checked];
    if (!ids.length) return;
    if (
      !window.confirm(
        `Delete ${ids.length} source${ids.length === 1 ? "" : "s"} from the library? ` +
          "This can't be undone."
      )
    )
      return;
    del.mutate(ids, {
      onSuccess: (r) => {
        toast.success(`Deleted ${r.deleted} source${r.deleted === 1 ? "" : "s"}`);
        setChecked(new Set());
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Delete failed"),
    });
  }

  return (
    <Page>
      <PageHeader
        icon={Layers}
        title="Sources"
        meta={sources?.length ? `${sources.length} scraped pages` : undefined}
      />

      <div className="grid min-h-0 flex-1 grid-cols-[360px_1fr]">
        {/* List */}
        <div className="flex min-h-0 flex-col border-r border-border">
          {canEdit && !!sources?.length && (
            <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
              <label className="flex items-center gap-2 text-muted-foreground">
                <input
                  type="checkbox"
                  checked={allChecked}
                  onChange={toggleAll}
                  className="size-3.5 accent-[rgb(var(--ember))]"
                />
                {checked.size ? `${checked.size} selected` : "Select all"}
              </label>
              {checked.size > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-7 text-muted-foreground hover:text-destructive"
                  onClick={onDelete}
                  disabled={del.isPending}
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
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {isLoading && (
              <p className="p-6 text-sm text-muted-foreground">Loading…</p>
            )}
            {sources?.length === 0 && (
              <p className="p-6 text-sm text-muted-foreground">
                No sources yet. Run research in the Research tab to scrape
                competitor pages — they&apos;ll appear here.
              </p>
            )}
            <ul>
              {sources?.map((s) => (
                <li key={s.id} className="flex items-stretch">
                  {canEdit && (
                    <label className="flex items-center border-b border-border pl-3 pr-1">
                      <input
                        type="checkbox"
                        checked={checked.has(s.id)}
                        onChange={() => toggle(s.id)}
                        className="size-3.5 accent-[rgb(var(--ember))]"
                      />
                    </label>
                  )}
                  <button
                    onClick={() => setSelected(s)}
                    className={`min-w-0 flex-1 border-b border-border px-4 py-3 text-left transition-colors ${
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
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="rounded bg-secondary px-1.5 py-0.5">
                        <span className="font-data">{s.word_count ?? "—"}</span>{" "}
                        words
                      </span>
                      <TrustBadge score={s.trust_score} reason={s.trust_reason} />
                      <span className="inline-flex items-center gap-1">
                        <FileText className="size-3" /> {s.run_topic}
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Detail */}
        <div className="min-h-0 overflow-y-auto">
          {!selected ? (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
              Select a source to read its scraped content.
            </div>
          ) : (
            <div className="mx-auto max-w-3xl px-8 py-6">
              <div className="mb-4">
                <h2 className="font-display text-xl font-bold">
                  {selected.title || selected.url}
                </h2>
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
                  <TrustBadge
                    score={selected.trust_score}
                    reason={selected.trust_reason}
                  />
                </div>
              </div>
              <SourceContentViewer sourceId={selected.source_id} />
            </div>
          )}
        </div>
      </div>
    </Page>
  );
}
