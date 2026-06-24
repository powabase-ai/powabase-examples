"use client";

import * as React from "react";
import {
  CheckCircle2,
  ExternalLink,
  Eye,
  Globe,
  Loader2,
  Trash2,
  Upload,
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
import { Textarea } from "@/components/ui/textarea";
import {
  useBrandMaterials,
  useIngestMaterials,
  useMaterialContent,
  useRemoveMaterial,
  useUploadMaterialFile,
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
  const [inspecting, setInspecting] = React.useState(false);

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
        <button
          type="button"
          aria-label="Inspect content"
          onClick={() => setInspecting(true)}
          className="inline-flex items-center text-muted-foreground transition-colors hover:text-foreground"
        >
          <Eye className="size-3.5" />
        </button>
        {canEdit && (
          <button
            type="button"
            aria-label="Remove page"
            onClick={() => {
              if (
                !window.confirm(
                  "Remove this page from the KB and delete its source?"
                )
              )
                return;
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
      <InspectDialog
        brandId={brandId}
        source={source}
        open={inspecting}
        onOpenChange={setInspecting}
      />
    </li>
  );
}

function InspectDialog({
  brandId,
  source,
  open,
  onOpenChange,
}: {
  brandId: string;
  source: BrandMaterialSource;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  // Only fetch while the dialog is open (rowId gates the query's `enabled`).
  const { data, isLoading, error } = useMaterialContent(
    brandId,
    open ? source.id : null
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-6">
            {source.title || source.url}
          </DialogTitle>
        </DialogHeader>
        {isLoading ? (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading scraped content…
          </div>
        ) : error ? (
          <p className="py-6 text-sm text-destructive">
            {error instanceof Error
              ? error.message
              : "No extracted content for this source yet."}
          </p>
        ) : (
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-3 font-data text-xs leading-relaxed">
            {data?.content?.trim() || "No content."}
          </pre>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function BrandMaterials({ brandId }: { brandId: string }) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data, isLoading } = useBrandMaterials(brandId);
  const ingest = useIngestMaterials(brandId);
  const upload = useUploadMaterialFile(brandId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

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

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!file) return;
    upload.mutate(file, {
      onSuccess: () => toast.success(`Uploading ${file.name}…`),
      onError: (err) =>
        toast.error(
          err instanceof Error ? err.message : "Could not upload file"
        ),
    });
  }

  const typedUrls = parseUrls(urls);
  const ingestDisabled = ingest.isPending || running;
  const uploadDisabled = upload.isPending || running;

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
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.md,.txt,.doc,.docx,.html"
                className="hidden"
                onChange={onPickFile}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadDisabled}
              >
                {upload.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Upload />
                )}
                Upload PDF/file
              </Button>
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
