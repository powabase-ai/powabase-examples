"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Download, Eye, Globe, Loader2, Undo2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { usePublications, usePublish, useUnpublish } from "@/lib/hooks/usePublish";
import { useArticle, useUpdateArticle } from "@/lib/hooks/useArticles";
import { useBrand } from "@/lib/hooks/useBrands";
import { exportArticle } from "@/lib/api";

function download(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  // Defer revoke so the browser has finished reading the blob before we free it.
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 0);
}

export function PublishDialog({
  open,
  onOpenChange,
  articleId,
  brandId,
  slug,
  published,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  articleId: string;
  brandId: string;
  slug?: string | null;
  published: boolean;
}) {
  const publish = usePublish(articleId, brandId);
  const unpublish = useUnpublish(articleId, brandId);
  const pubs = usePublications(articleId);
  const { data: article } = useArticle(articleId);
  const { data: brand } = useBrand(brandId);
  const updateArticle = useUpdateArticle(articleId);

  const [busy, setBusy] = useState<string | null>(null);
  const [urlValue, setUrlValue] = useState("");

  // Where the article actually lives: an explicit override, else derived from the
  // brand's blog URL pattern. RankForge's own /p/{id} page is just a local preview.
  const computed = useMemo(
    () =>
      brand?.url_pattern && article?.slug
        ? brand.url_pattern
            .replace("{slug}", article.slug)
            .replace("{id}", article.id)
        : null,
    [brand?.url_pattern, article?.slug, article?.id]
  );
  const blogUrl = (article?.canonical_url || "").trim() || computed;
  const previewUrl =
    typeof window !== "undefined" ? `${window.location.origin}/p/${articleId}` : "";

  // Auto-fill the article's blog URL from a saved value, else the brand's URL pattern.
  useEffect(
    () => setUrlValue(article?.canonical_url || computed || ""),
    [article?.canonical_url, computed]
  );
  // "Dirty" means the field differs from the auto-derived value, so Save is disabled
  // when urlValue equals the brand-pattern URL — that's intentional, not a bug. The
  // derived URL is ephemeral by design: there's no need to persist it to canonical_url
  // because the backend re-derives it from the brand's url_pattern on export/publish.
  // We only persist an explicit override the user typed.
  const urlDirty = urlValue.trim() !== (article?.canonical_url || computed || "");

  async function onExport(format: "markdown" | "html") {
    setBusy(format);
    try {
      const text = await exportArticle(articleId, format);
      download(
        `${slug || "article"}.${format === "markdown" ? "md" : "html"}`,
        text,
        format === "markdown" ? "text/markdown" : "text/html"
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(null);
    }
  }

  async function copyHtml() {
    setBusy("copy");
    try {
      await navigator.clipboard.writeText(await exportArticle(articleId, "html"));
      toast.success("HTML copied");
    } catch {
      toast.error("Copy failed");
    } finally {
      setBusy(null);
    }
  }

  function saveUrl() {
    updateArticle.mutate(
      { canonical_url: urlValue.trim() },
      {
        onSuccess: () => toast.success("Published URL saved"),
        onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
      }
    );
  }

  function markAsPublished() {
    publish.mutate(
      { target_type: "export" },
      {
        onSuccess: (p) =>
          p.status === "success"
            ? toast.success("Marked as published")
            : toast.error("Couldn’t mark published"),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Publish failed"),
      }
    );
  }

  function onUnpublish() {
    if (
      !window.confirm(
        "Unpublish this article? It goes back to draft and leaves its cluster — use " +
          "when you've taken it down from your blog. Other articles that cite it will " +
          "flag broken links until you re-publish or fix them."
      )
    )
      return;
    unpublish.mutate(undefined, {
      onSuccess: () => toast.success("Unpublished — back to draft"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
    });
  }

  const h3 = "text-xs font-semibold uppercase tracking-wide text-muted-foreground";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Publish &amp; export</DialogTitle>
        </DialogHeader>
        <div className="min-w-0 space-y-5">
          <section>
            <h3 className={h3}>Published on your blog</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Record where this article lives on your blog. Internal links from your
              other articles point to this URL — so publish the article at exactly this
              address, otherwise those citations break. Use Export below to get the file.
            </p>

            <div className="mt-2 space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                This article’s URL on your blog
              </label>
              <div className="flex gap-2">
                <Input
                  value={urlValue}
                  onChange={(e) => setUrlValue(e.target.value)}
                  placeholder={computed ?? "https://blog.example.com/my-article"}
                  className="h-8 text-xs"
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0"
                  onClick={saveUrl}
                  disabled={!urlDirty || updateArticle.isPending}
                >
                  {updateArticle.isPending && <Loader2 className="animate-spin" />}
                  Save
                </Button>
              </div>
              <p className="text-[11px] break-all text-muted-foreground">
                {!urlValue.trim()
                  ? "Add a blog URL pattern in brand settings to auto-fill this, or type the exact URL."
                  : computed && urlValue.trim() === computed
                    ? "Auto-filled from your brand URL pattern."
                    : "Custom URL for this article — internal links point here."}
              </p>
            </div>

            <div className="mt-3 flex items-center gap-2">
              <Button
                variant={published ? "outline" : "gold"}
                size="sm"
                className="shrink-0"
                onClick={markAsPublished}
                disabled={publish.isPending}
              >
                {publish.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : published ? (
                  <Check />
                ) : (
                  <Globe />
                )}
                {published ? "Published — re-mark" : "Mark as published"}
              </Button>
              {published && blogUrl && (
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  live at{" "}
                  <a
                    href={blogUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary hover:underline"
                  >
                    {blogUrl}
                  </a>
                </span>
              )}
            </div>

            {published && (
              <button
                type="button"
                onClick={onUnpublish}
                disabled={unpublish.isPending}
                className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
              >
                {unpublish.isPending ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  <Undo2 className="size-3" />
                )}
                Unpublish — remove from blog &amp; cluster
              </button>
            )}
          </section>

          <section>
            <h3 className={h3}>Preview</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              See how the article renders as a standalone styled page before you put it
              on your blog. This is just a local preview — RankForge isn’t your host.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              disabled={!published}
              onClick={() => window.open(previewUrl, "_blank", "noopener")}
            >
              <Eye /> Open preview
            </Button>
            {!published && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Mark the article published to open its preview.
              </p>
            )}
          </section>

          <section>
            <h3 className={h3}>Export</h3>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onExport("markdown")}
                disabled={busy !== null}
              >
                {busy === "markdown" ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Download />
                )}
                Markdown
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onExport("html")}
                disabled={busy !== null}
              >
                {busy === "html" ? <Loader2 className="animate-spin" /> : <Download />}
                HTML
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={copyHtml}
                disabled={busy !== null}
              >
                {busy === "copy" ? <Loader2 className="animate-spin" /> : <Copy />}
                Copy HTML
              </Button>
            </div>
          </section>

          {pubs.data && pubs.data.length > 0 && (
            <section>
              <h3 className={h3}>Recent</h3>
              <div className="mt-2 space-y-1.5 text-xs">
                {pubs.data.slice(0, 5).map((p) => (
                  <div key={p.id} className="flex items-center gap-3">
                    <span className="w-16 capitalize">
                      {p.target_type === "export" ? "Published" : p.target_type}
                    </span>
                    <span
                      className={
                        p.status === "success"
                          ? "text-[rgb(var(--success))]"
                          : "text-destructive"
                      }
                    >
                      {p.status}
                    </span>
                    <span className="ml-auto text-muted-foreground">
                      {new Date(p.created_at).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
