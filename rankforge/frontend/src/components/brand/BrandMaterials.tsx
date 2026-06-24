"use client";

import * as React from "react";
import {
  CheckCircle2,
  ExternalLink,
  Globe,
  Loader2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  useBrandMaterials,
  useIngestMaterials,
  useRemoveMaterial,
} from "@/lib/hooks/useBrandMaterials";
import { useAuth } from "@/lib/auth/AuthProvider";
import {
  canApprove,
  materialsRunning,
  type BrandMaterialSource,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Parse a comma/newline-separated blob into trimmed, non-empty URLs. */
function parseUrls(raw: string): string[] {
  return raw
    .split(/[\n,]+/)
    .map((u) => u.trim())
    .filter(Boolean);
}

function statusColor(status?: string | null) {
  switch (status) {
    case "extracted":
    case "indexed":
    case "done":
      return "var(--success)";
    case "failed":
    case "error":
      return "var(--destructive)";
    default:
      return "var(--muted-ink)";
  }
}

function MaterialRow({
  brandId,
  source,
  canEdit,
}: {
  brandId: string;
  source: BrandMaterialSource;
  canEdit: boolean;
}) {
  const remove = useRemoveMaterial(brandId);

  return (
    <li className="flex items-center gap-3 border-b border-border px-3 py-2.5 last:border-b-0">
      <div className="min-w-0 flex-1">
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex max-w-full items-center gap-1 truncate text-sm font-medium hover:underline"
        >
          <span className="truncate">{source.title || source.url}</span>
          <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
        </a>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {source.url}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2 text-xs">
        {source.status && (
          <span
            className="rounded px-1.5 py-0.5 font-data capitalize"
            style={{
              color: `rgb(${statusColor(source.status)})`,
              background: `rgb(${statusColor(source.status)} / 0.12)`,
            }}
          >
            {source.status}
          </span>
        )}
        <span className="rounded bg-secondary px-1.5 py-0.5 capitalize text-muted-foreground">
          {source.origin}
        </span>
        {canEdit && (
          <button
            type="button"
            aria-label="Remove page"
            onClick={() => {
              remove.mutate(source.id, {
                onError: (e) =>
                  toast.error(
                    e instanceof Error ? e.message : "Could not remove page"
                  ),
              });
            }}
            disabled={remove.isPending}
            className="inline-flex items-center text-muted-foreground transition-colors hover:text-destructive disabled:opacity-50"
          >
            {remove.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Trash2 className="size-3.5" />
            )}
          </button>
        )}
      </div>
    </li>
  );
}

export function BrandMaterials({ brandId }: { brandId: string }) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data, isLoading } = useBrandMaterials(brandId);
  const ingest = useIngestMaterials(brandId);

  const [urls, setUrls] = React.useState("");

  const running = materialsRunning(data?.progress);
  const sources = data?.sources ?? [];

  function startIngest(typed: string[]) {
    ingest.mutate(typed, {
      onSuccess: () => {
        toast.success("Ingesting brand materials…");
        if (typed.length) setUrls("");
      },
      onError: (e) =>
        toast.error(
          e instanceof Error ? e.message : "Could not start ingest"
        ),
    });
  }

  const typedUrls = parseUrls(urls);
  const ingestDisabled = ingest.isPending || running;

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="text-base">Brand materials</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Pages from your own site/docs that drafts can describe and link to
          (internal links). Set a sitemap on the brand to crawl automatically,
          or add specific URLs.
        </p>

        {/* KB status / live progress */}
        {running ? (
          <div className="flex items-start gap-2.5 rounded-md border border-[rgb(var(--ember))]/30 bg-[rgb(var(--ember))]/[0.06] px-3 py-2.5">
            <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-[rgb(var(--ember-bright))]" />
            <div className="min-w-0 text-sm font-medium text-foreground">
              {data?.progress?.message ?? "Ingesting brand materials…"}
            </div>
          </div>
        ) : data?.kb_ready ? (
          <div className="inline-flex items-center gap-1.5 text-xs text-[rgb(var(--success))]">
            <CheckCircle2 className="size-3.5" /> Materials KB is ready
          </div>
        ) : null}

        {/* Add / ingest controls */}
        {canEdit && (
          <div className="space-y-2">
            <Textarea
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              placeholder="https://yoursite.com/about, https://yoursite.com/docs/…&#10;(one per line or comma-separated — optional)"
              className="min-h-[64px] font-data text-xs"
              disabled={ingestDisabled}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => startIngest([])}
                disabled={ingestDisabled}
              >
                {ingestDisabled ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Globe />
                )}
                Ingest from sitemap
              </Button>
              {typedUrls.length > 0 && (
                <Button
                  variant="gold"
                  size="sm"
                  onClick={() => startIngest(typedUrls)}
                  disabled={ingestDisabled}
                >
                  {ingestDisabled && <Loader2 className="animate-spin" />}
                  Add &amp; ingest ({typedUrls.length})
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Source list / empty state */}
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : sources.length > 0 ? (
          <ul className={cn("rounded-md border border-border")}>
            {sources.map((s) => (
              <MaterialRow
                key={s.id}
                brandId={brandId}
                source={s}
                canEdit={canEdit}
              />
            ))}
          </ul>
        ) : !running ? (
          <div className="rounded-md border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
            No brand pages yet.{" "}
            {canEdit
              ? "Ingest from your sitemap or add URLs above to build the materials KB."
              : "An editor can add pages to build the materials KB."}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
