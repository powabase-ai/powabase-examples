"use client";

import * as React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Eye,
  Loader2,
  Plus,
  RefreshCw,
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
  useBulkDeleteMaterials,
  useDiscoverMaterials,
  useIngestMaterials,
  useMaterialContent,
  useRefreshMaterials,
  useRemoveMaterial,
  useUploadMaterialFile,
} from "@/lib/hooks/useBrandMaterials";
import { useAuth } from "@/lib/auth/AuthProvider";
import {
  canApprove,
  materialsRunning,
  type BrandMaterialSource,
  type MaterialsDiscovery,
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
  selected,
  onToggleSelect,
  disabled,
}: {
  brandId: string;
  source: BrandMaterialSource;
  canEdit: boolean;
  selected: boolean;
  onToggleSelect: (id: string, checked: boolean) => void;
  disabled: boolean;
}) {
  const remove = useRemoveMaterial(brandId);
  const [inspecting, setInspecting] = React.useState(false);

  return (
    <li className="flex items-center gap-3 border-b border-border px-3 py-2.5 last:border-b-0">
      {canEdit && (
        <input
          type="checkbox"
          aria-label="Select page"
          checked={selected}
          disabled={disabled}
          onChange={(e) => onToggleSelect(source.id, e.target.checked)}
          className="size-3.5 shrink-0 cursor-pointer accent-[rgb(var(--ember))] disabled:cursor-not-allowed"
        />
      )}
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
  const discover = useDiscoverMaterials(brandId);
  const [mode, setMode] = React.useState<IngestMode>("sitemap");
  const [url, setUrl] = React.useState("");
  const [urlsText, setUrlsText] = React.useState("");
  // String-backed so the field can be cleared/edited freely; clamped at use.
  const [maxPagesStr, setMaxPagesStr] = React.useState("30");
  const maxPages = Math.max(1, Math.min(200, parseInt(maxPagesStr, 10) || 30));
  // Crawl preview: discovered pages (grouped by subdomain) + which hosts to keep.
  const [found, setFound] = React.useState<MaterialsDiscovery | null>(null);
  const [keepHosts, setKeepHosts] = React.useState<Set<string>>(new Set());

  // Reset inputs + preview when the dialog closes, so reopening starts clean.
  React.useEffect(() => {
    if (!open) {
      setFound(null);
      setUrl("");
      setUrlsText("");
    }
  }, [open]);

  function pickMode(m: IngestMode) {
    setMode(m);
    setFound(null); // leaving/entering crawl resets any preview
    setUrl(""); // url is shared across tabs — don't bleed a sitemap URL into crawl
    setUrlsText("");
  }

  function startIngest(body: MaterialsIngestRequest, label: string) {
    ingest.mutate(body, {
      onSuccess: () => {
        toast.success(label);
        setUrl("");
        setUrlsText("");
        setFound(null);
        onOpenChange(false);
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start ingest"),
    });
  }

  function runDiscover() {
    if (!url.trim()) return toast.error("Enter a site URL to crawl");
    discover.mutate(
      { url: url.trim(), maxPages },
      {
        onSuccess: (res) => {
          if (!res.total) {
            toast.error("No pages found — try a different URL.");
            return;
          }
          setFound(res);
          setKeepHosts(new Set(res.hosts.map((h) => h.host)));
        },
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Discovery failed"),
      }
    );
  }

  function submit() {
    if (mode === "sitemap") {
      const body: MaterialsIngestRequest = { mode, max_pages: maxPages };
      if (url.trim()) body.url = url.trim(); // else backend uses the saved sitemap
      startIngest(body, "Ingesting brand materials…");
    } else {
      const list = parseUrls(urlsText);
      if (!list.length) return toast.error("Paste at least one URL");
      startIngest({ mode: "urls", urls: list }, "Ingesting brand materials…");
    }
  }

  function confirmCrawl() {
    const urls = (found?.hosts ?? [])
      .filter((h) => keepHosts.has(h.host))
      .flatMap((h) => h.urls);
    if (!urls.length) return toast.error("Select at least one subdomain");
    // Ingest the confirmed pages, tagged as crawl provenance.
    startIngest(
      { mode: "urls", urls, origin: "crawl", max_pages: maxPages },
      `Ingesting ${urls.length} page${urls.length === 1 ? "" : "s"}…`
    );
  }

  const urlCount = parseUrls(urlsText).length;
  const selectedCount = (found?.hosts ?? [])
    .filter((h) => keepHosts.has(h.host))
    .reduce((n, h) => n + h.urls.length, 0);

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
              onClick={() => pickMode(m.key)}
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

        {/* crawl: step 1 = enter URL & discover; step 2 = confirm subdomains */}
        {mode === "crawl" && !found && (
          <div className="space-y-1.5">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://yoursite.com"
            />
            <p className="text-xs text-muted-foreground">
              No sitemap needed — we&apos;ll find pages across your domain and
              subdomains, then let you confirm before importing.
            </p>
          </div>
        )}
        {mode === "crawl" && found && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              Found {found.total} page{found.total === 1 ? "" : "s"} across{" "}
              {found.hosts.length} subdomain
              {found.hosts.length === 1 ? "" : "s"}. Choose what to ingest:
            </p>
            <ul className="max-h-56 space-y-0.5 overflow-y-auto rounded-md border border-border p-1">
              {found.hosts.map((h) => (
                <li key={h.host}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-secondary">
                    <input
                      type="checkbox"
                      checked={keepHosts.has(h.host)}
                      onChange={(e) =>
                        setKeepHosts((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(h.host);
                          else next.delete(h.host);
                          return next;
                        })
                      }
                    />
                    <span className="min-w-0 flex-1 truncate font-medium">
                      {h.host}
                    </span>
                    <span className="shrink-0 font-data text-xs text-muted-foreground">
                      {h.urls.length}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
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

        {/* page cap for discovery modes (hidden once a crawl preview is shown) */}
        {mode !== "urls" && !(mode === "crawl" && found) && (
          <label className="flex items-center justify-between gap-3 text-sm">
            <span className="text-muted-foreground">Max pages</span>
            <Input
              type="number"
              min={1}
              max={200}
              value={maxPagesStr}
              onChange={(e) => setMaxPagesStr(e.target.value)}
              className="w-24"
            />
          </label>
        )}

        <DialogFooter>
          {mode === "crawl" && found ? (
            <>
              <Button variant="outline" size="sm" onClick={() => setFound(null)}>
                Back
              </Button>
              <Button
                variant="gold"
                size="sm"
                onClick={confirmCrawl}
                disabled={ingest.isPending || disabled || selectedCount === 0}
              >
                {ingest.isPending && <Loader2 className="animate-spin" />}
                Ingest {selectedCount} page{selectedCount === 1 ? "" : "s"}
              </Button>
            </>
          ) : mode === "crawl" ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                variant="gold"
                size="sm"
                onClick={runDiscover}
                disabled={discover.isPending || disabled}
              >
                {discover.isPending && <Loader2 className="animate-spin" />}
                Discover pages
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
              >
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
            </>
          )}
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
  const refresh = useRefreshMaterials(brandId);
  const bulkDelete = useBulkDeleteMaterials(brandId);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const [addOpen, setAddOpen] = React.useState(false);
  // Checkbox selection for bulk refresh / delete (ids of selected source rows).
  const [selected, setSelected] = React.useState<Set<string>>(new Set());

  const running = materialsRunning(data?.progress);
  const sources = data?.sources ?? [];

  // Drop any selected ids that no longer exist (e.g. after a delete completes), so a
  // stale selection can't linger or target a removed row.
  React.useEffect(() => {
    const ids = new Set(sources.map((s) => s.id));
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [sources]);

  function toggleSelect(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const allSelected = sources.length > 0 && selected.size === sources.length;
  function toggleAll(checked: boolean) {
    setSelected(checked ? new Set(sources.map((s) => s.id)) : new Set());
  }

  function runRefresh() {
    const ids = [...selected];
    if (!ids.length) return;
    refresh.mutate(ids, {
      onSuccess: () => {
        toast.success(`Refreshing ${ids.length} page${ids.length === 1 ? "" : "s"}…`);
        setSelected(new Set());
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start refresh"),
    });
  }

  function runBulkDelete() {
    const ids = [...selected];
    if (!ids.length) return;
    if (
      !window.confirm(
        `Delete ${ids.length} selected page${ids.length === 1 ? "" : "s"} from the KB?`
      )
    )
      return;
    bulkDelete.mutate(ids, {
      onSuccess: () => {
        toast.success(`Removing ${ids.length} page${ids.length === 1 ? "" : "s"}…`);
        setSelected(new Set());
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not delete pages"),
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

        {/* Bulk-selection toolbar */}
        {canEdit && sources.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <label className="inline-flex cursor-pointer items-center gap-2 text-muted-foreground">
              <input
                type="checkbox"
                aria-label="Select all pages"
                checked={allSelected}
                disabled={running}
                onChange={(e) => toggleAll(e.target.checked)}
                className="size-3.5 cursor-pointer accent-[rgb(var(--ember))]"
              />
              {selected.size > 0 ? `${selected.size} selected` : "Select all"}
            </label>
            {selected.size > 0 && (
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={runRefresh}
                  disabled={running || refresh.isPending}
                  title="Re-scrape selected pages to pick up changed content"
                >
                  {refresh.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <RefreshCw />
                  )}
                  Refresh
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={runBulkDelete}
                  disabled={running || bulkDelete.isPending}
                  className="text-destructive hover:text-destructive"
                >
                  {bulkDelete.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Trash2 />
                  )}
                  Delete
                </Button>
              </div>
            )}
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
                selected={selected.has(s.id)}
                onToggleSelect={toggleSelect}
                disabled={running}
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
