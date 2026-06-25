"use client";

import * as React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Eye,
  Loader2,
  Plus,
  Trash2,
  Upload,
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
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
  type MaterialsIngestRequest,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type IngestMode = "sitemap" | "crawl" | "urls";

const INGEST_MODES: { key: IngestMode; label: string }[] = [
  { key: "sitemap", label: "Sitemap" },
  { key: "crawl", label: "Crawl site" },
  { key: "urls", label: "Paste URLs" },
];

// Powabase extracts these natively; mirror its supported set (PDF, Word, slides,
// sheets, markdown/text, HTML).
const UPLOAD_ACCEPT =
  ".pdf,.doc,.docx,.md,.markdown,.txt,.rtf,.html,.htm,.pptx,.xlsx,.csv";

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

function AddPagesDialog({
  brandId,
  open,
  onOpenChange,
  disabled,
}: {
  brandId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  disabled: boolean;
}) {
  const ingest = useIngestMaterials(brandId);
  const [mode, setMode] = React.useState<IngestMode>("sitemap");
  const [url, setUrl] = React.useState("");
  const [urlsText, setUrlsText] = React.useState("");
  const [maxPages, setMaxPages] = React.useState(30);

  function submit() {
    const body: MaterialsIngestRequest = { mode, max_pages: maxPages };
    if (mode === "crawl") {
      if (!url.trim()) return toast.error("Enter a site URL to crawl");
      body.url = url.trim();
    } else if (mode === "sitemap") {
      if (url.trim()) body.url = url.trim(); // else backend uses the saved sitemap
    } else {
      const list = parseUrls(urlsText);
      if (!list.length) return toast.error("Paste at least one URL");
      body.urls = list;
    }
    ingest.mutate(body, {
      onSuccess: () => {
        toast.success("Ingesting brand materials…");
        setUrl("");
        setUrlsText("");
        onOpenChange(false);
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start ingest"),
    });
  }

  const urlCount = parseUrls(urlsText).length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add brand pages</DialogTitle>
          <DialogDescription>
            Pull pages from your own site so drafts can describe and link to them.
          </DialogDescription>
        </DialogHeader>

        {/* discovery mode */}
        <div className="flex gap-1 rounded-md bg-secondary p-1">
          {INGEST_MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMode(m.key)}
              className={cn(
                "flex-1 rounded px-2 py-1.5 text-xs font-medium transition-colors",
                mode === m.key
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {m.label}
            </button>
          ))}
        </div>

        {mode === "sitemap" && (
          <div className="space-y-1.5">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://yoursite.com/sitemap.xml"
            />
            <p className="text-xs text-muted-foreground">
              Leave blank to use the sitemap saved in Settings.
            </p>
          </div>
        )}
        {mode === "crawl" && (
          <div className="space-y-1.5">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://yoursite.com"
            />
            <p className="text-xs text-muted-foreground">
              No sitemap needed — we crawl from this page to discover related
              pages.
            </p>
          </div>
        )}
        {mode === "urls" && (
          <div className="space-y-1.5">
            <Textarea
              value={urlsText}
              onChange={(e) => setUrlsText(e.target.value)}
              placeholder="https://yoursite.com/about&#10;https://yoursite.com/docs/…"
              className="min-h-[88px] font-data text-xs"
            />
            <p className="text-xs text-muted-foreground">
              One per line or comma-separated.
              {urlCount > 0 ? ` ${urlCount} URL${urlCount === 1 ? "" : "s"}.` : ""}
            </p>
          </div>
        )}

        {mode !== "urls" && (
          <label className="flex items-center justify-between gap-3 text-sm">
            <span className="text-muted-foreground">Max pages</span>
            <Input
              type="number"
              min={1}
              max={200}
              value={maxPages}
              onChange={(e) =>
                setMaxPages(Math.max(1, Math.min(200, Number(e.target.value) || 1)))
              }
              className="w-24"
            />
          </label>
        )}

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="gold"
            size="sm"
            onClick={submit}
            disabled={ingest.isPending || disabled}
          >
            {ingest.isPending && <Loader2 className="animate-spin" />}
            Start ingest
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function BrandMaterials({ brandId }: { brandId: string }) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data, isLoading } = useBrandMaterials(brandId);
  const upload = useUploadMaterialFile(brandId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const [addOpen, setAddOpen] = React.useState(false);

  const running = materialsRunning(data?.progress);
  const sources = data?.sources ?? [];

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

  const uploadDisabled = upload.isPending || running;
  const failed = data?.progress?.phase === "failed";

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="text-base">Brand materials</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Pages from your own site/docs that drafts can describe and link to
          (internal links). Crawl your site, pull from a sitemap, add specific
          URLs, or upload files (PDF, Word, Markdown).
        </p>

        {/* KB status / live progress */}
        {running ? (
          <div className="flex items-start gap-2.5 rounded-md border border-[rgb(var(--ember))]/30 bg-[rgb(var(--ember))]/[0.06] px-3 py-2.5">
            <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-[rgb(var(--ember-bright))]" />
            <div className="min-w-0 text-sm font-medium text-foreground">
              {data?.progress?.message ?? "Ingesting brand materials…"}
            </div>
          </div>
        ) : failed ? (
          <div className="flex items-start gap-2.5 rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
            <div className="min-w-0 text-sm">
              <span className="font-medium text-destructive">
                Last ingest didn&apos;t finish.
              </span>{" "}
              <span className="text-muted-foreground">
                {data?.progress?.message ?? "Please try again."}
              </span>
            </div>
          </div>
        ) : sources.length > 0 ? (
          <div className="inline-flex items-center gap-1.5 text-xs text-[rgb(var(--success))]">
            <CheckCircle2 className="size-3.5" /> Materials KB is ready —{" "}
            {sources.length} page{sources.length === 1 ? "" : "s"}
          </div>
        ) : null}

        {/* Add / ingest controls */}
        {canEdit && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="gold"
                size="sm"
                onClick={() => setAddOpen(true)}
                disabled={running}
              >
                {running ? <Loader2 className="animate-spin" /> : <Plus />}
                Add pages
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept={UPLOAD_ACCEPT}
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
                Upload file
              </Button>
            </div>
            <AddPagesDialog
              brandId={brandId}
              open={addOpen}
              onOpenChange={setAddOpen}
              disabled={running}
            />
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
              ? "Add pages or upload files above to build the materials KB."
              : "An editor can add pages to build the materials KB."}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
