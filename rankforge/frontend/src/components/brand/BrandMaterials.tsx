"use client";

import * as React from "react";
import {
  AlertTriangle,
  ExternalLink,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { SourceContentViewer } from "@/components/SourceContentViewer";
import {
  useBrandMaterials,
  useBulkDeleteMaterials,
  useDiscoverMaterials,
  useIngestMaterials,
  useRefreshMaterials,
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

/** One rail row: bulk-select checkbox + a button that shows the page's content in the
 *  main pane. */
function MaterialRow({
  source,
  canEdit,
  active,
  checked,
  onToggleCheck,
  onSelect,
  disabled,
}: {
  source: BrandMaterialSource;
  canEdit: boolean;
  active: boolean;
  checked: boolean;
  onToggleCheck: (id: string, checked: boolean) => void;
  onSelect: () => void;
  disabled: boolean;
}) {
  return (
    <li className="flex items-stretch">
      {canEdit && (
        <label className="flex items-center border-b border-border pl-3 pr-1">
          <input
            type="checkbox"
            aria-label="Select page"
            checked={checked}
            disabled={disabled}
            onChange={(e) => onToggleCheck(source.id, e.target.checked)}
            className="size-3.5 cursor-pointer accent-[rgb(var(--ember))] disabled:cursor-not-allowed"
          />
        </label>
      )}
      <button
        onClick={onSelect}
        className={`min-w-0 flex-1 border-b border-border px-4 py-3 text-left transition-colors ${
          active ? "bg-[rgb(var(--accent-gold-muted))]" : "hover:bg-secondary"
        }`}
      >
        <div className="line-clamp-1 text-sm font-medium">
          {source.title || source.url}
        </div>
        <div className="mt-1 line-clamp-1 text-xs text-muted-foreground">
          {source.url}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
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
        </div>
      </button>
    </li>
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
  const [checked, setChecked] = React.useState<Set<string>>(new Set());
  const [activeId, setActiveId] = React.useState<string | null>(null);

  const running = materialsRunning(data?.progress);
  const sources = React.useMemo(() => data?.sources ?? [], [data?.sources]);
  const active = sources.find((s) => s.id === activeId) ?? null;

  // Drop stale ids (checkbox selection + active) after the list changes.
  React.useEffect(() => {
    const ids = new Set(sources.map((s) => s.id));
    setChecked((prev) => {
      const next = new Set([...prev].filter((x) => ids.has(x)));
      return next.size === prev.size ? prev : next;
    });
    setActiveId((prev) => (prev && ids.has(prev) ? prev : null));
  }, [sources]);

  function toggleCheck(id: string, on: boolean) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  const allChecked = sources.length > 0 && checked.size === sources.length;
  function toggleAll(on: boolean) {
    setChecked(on ? new Set(sources.map((s) => s.id)) : new Set());
  }

  function runRefresh() {
    const ids = [...checked];
    if (!ids.length) return;
    refresh.mutate(ids, {
      onSuccess: () => {
        toast.success(`Refreshing ${ids.length} page${ids.length === 1 ? "" : "s"}…`);
        setChecked(new Set());
      },
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start refresh"),
    });
  }

  function runBulkDelete() {
    const ids = [...checked];
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
        setChecked(new Set());
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
        toast.error(err instanceof Error ? err.message : "Could not upload file"),
    });
  }

  const uploadDisabled = upload.isPending || running;
  const failed = data?.progress?.phase === "failed";

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[360px_1fr]">
      {/* Rail */}
      <div className="flex min-h-0 flex-col border-r border-border">
        {/* Ingest controls */}
        {canEdit && (
          <div className="flex flex-wrap gap-2 border-b border-border p-2.5">
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
              {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              Upload file
            </Button>
            <AddPagesDialog
              brandId={brandId}
              open={addOpen}
              onOpenChange={setAddOpen}
              disabled={running}
            />
          </div>
        )}

        {/* Live status */}
        {running ? (
          <div className="flex items-center gap-2 border-b border-border bg-[rgb(var(--ember))]/[0.06] px-3 py-2 text-xs text-foreground">
            <Loader2 className="size-3.5 shrink-0 animate-spin text-[rgb(var(--ember-bright))]" />
            <span className="min-w-0 truncate">
              {data?.progress?.message ?? "Ingesting brand materials…"}
            </span>
          </div>
        ) : failed ? (
          <div className="flex items-center gap-2 border-b border-border bg-destructive/[0.06] px-3 py-2 text-xs">
            <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
            <span className="min-w-0 truncate">
              <span className="font-medium text-destructive">
                Last ingest didn&apos;t finish.
              </span>{" "}
              <span className="text-muted-foreground">
                {data?.progress?.message ?? "Please try again."}
              </span>
            </span>
          </div>
        ) : null}

        {/* Bulk toolbar */}
        {canEdit && sources.length > 0 && (
          <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
            <label className="flex cursor-pointer items-center gap-2 text-muted-foreground">
              <input
                type="checkbox"
                aria-label="Select all pages"
                checked={allChecked}
                disabled={running}
                onChange={(e) => toggleAll(e.target.checked)}
                className="size-3.5 cursor-pointer accent-[rgb(var(--ember))]"
              />
              {checked.size ? `${checked.size} selected` : "Select all"}
            </label>
            {checked.size > 0 && (
              <div className="ml-auto flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7"
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
                  variant="ghost"
                  size="sm"
                  className="h-7 text-muted-foreground hover:text-destructive"
                  onClick={runBulkDelete}
                  disabled={running || bulkDelete.isPending}
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

        {/* Rows */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <p className="p-6 text-sm text-muted-foreground">Loading…</p>
          ) : sources.length > 0 ? (
            <ul>
              {sources.map((s) => (
                <MaterialRow
                  key={s.id}
                  source={s}
                  canEdit={canEdit}
                  active={activeId === s.id}
                  checked={checked.has(s.id)}
                  onToggleCheck={toggleCheck}
                  onSelect={() => setActiveId(s.id)}
                  disabled={running}
                />
              ))}
            </ul>
          ) : !running ? (
            <div className="p-6 text-sm text-muted-foreground">
              No brand pages yet.{" "}
              {canEdit
                ? "Add pages or upload files above to build the materials KB."
                : "An editor can add pages to build the materials KB."}
            </div>
          ) : null}
        </div>
      </div>

      {/* Detail */}
      <div className="min-h-0 overflow-y-auto">
        {!active ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
            Select a page to read its extracted content.
          </div>
        ) : (
          <div className="mx-auto max-w-3xl px-8 py-6">
            <div className="mb-4">
              <h2 className="font-display text-xl font-bold">
                {active.title || active.url}
              </h2>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <a
                  href={active.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 hover:underline"
                >
                  <ExternalLink className="size-3" /> {active.url}
                </a>
                {active.status && (
                  <span
                    className="rounded px-1.5 py-0.5 font-data capitalize"
                    style={{
                      color: `rgb(${statusColor(active.status)})`,
                      background: `rgb(${statusColor(active.status)} / 0.12)`,
                    }}
                  >
                    {active.status}
                  </span>
                )}
                <span className="capitalize">{active.origin}</span>
              </div>
            </div>
            <SourceContentViewer
              sourceId={active.source_id ?? null}
              emptyHint="This page hasn't been extracted yet — check back once ingestion finishes."
            />
          </div>
        )}
      </div>
    </div>
  );
}
