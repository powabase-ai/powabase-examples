"use client";

import { useState } from "react";
import { Copy, Download, Globe, Loader2, Send } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { usePublications, usePublish } from "@/lib/hooks/usePublish";
import { exportArticle, type PublishTarget } from "@/lib/api";

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
  slug,
  published,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  articleId: string;
  slug?: string | null;
  published: boolean;
}) {
  const publish = usePublish(articleId);
  const pubs = usePublications(articleId);
  const [webhook, setWebhook] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const publicUrl =
    typeof window !== "undefined" ? `${window.location.origin}/p/${articleId}` : "";

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

  function doPublish(target: PublishTarget) {
    publish.mutate(
      target === "webhook"
        ? { target_type: "webhook", config: { url: webhook.trim() } }
        : { target_type: "export" },
      {
        onSuccess: (p) =>
          p.status === "success"
            ? toast.success(
                target === "webhook" ? "Sent to webhook" : "Published — now crawlable"
              )
            : toast.error(
                target === "webhook" ? "Webhook failed" : "Publish failed"
              ),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Publish failed"),
      }
    );
  }

  const h3 = "text-xs font-semibold uppercase tracking-wide text-muted-foreground";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Publish &amp; export</DialogTitle>
        </DialogHeader>
        <div className="space-y-5">
          <section>
            <h3 className={h3}>Public page</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Publishing serves a server-rendered page with schema.org JSON-LD that
              search &amp; answer engines can crawl.
            </p>
            {published ? (
              <div className="mt-2 flex items-center gap-2">
                <a
                  href={publicUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-w-0 items-center gap-1.5 truncate text-sm text-primary hover:underline"
                >
                  <Globe className="size-4 shrink-0" />
                  <span className="truncate">{publicUrl}</span>
                </a>
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto shrink-0"
                  onClick={() => {
                    navigator.clipboard.writeText(publicUrl);
                    toast.success("Link copied");
                  }}
                >
                  <Copy /> Copy
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0"
                  onClick={() => doPublish("export")}
                  disabled={publish.isPending}
                >
                  Re-publish
                </Button>
              </div>
            ) : (
              <Button
                variant="gold"
                size="sm"
                className="mt-2"
                onClick={() => doPublish("export")}
                disabled={publish.isPending}
              >
                {publish.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Globe />
                )}
                Publish (make crawlable)
              </Button>
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

          <section>
            <h3 className={h3}>Webhook</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              POST the article (HTML, Markdown, JSON-LD, public URL) to a CMS or
              automation endpoint.
            </p>
            <div className="mt-2 flex gap-2">
              <Input
                value={webhook}
                onChange={(e) => setWebhook(e.target.value)}
                placeholder="https://hooks.example.com/…"
              />
              <Button
                size="sm"
                variant="outline"
                className="shrink-0"
                onClick={() => doPublish("webhook")}
                disabled={publish.isPending || !webhook.trim()}
              >
                {publish.isPending ? <Loader2 className="animate-spin" /> : <Send />}
                Send
              </Button>
            </div>
          </section>

          {pubs.data && pubs.data.length > 0 && (
            <section>
              <h3 className={h3}>Recent</h3>
              <div className="mt-2 space-y-1.5 text-xs">
                {pubs.data.slice(0, 5).map((p) => (
                  <div key={p.id} className="flex items-center gap-3">
                    <span className="w-16 capitalize">{p.target_type}</span>
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
