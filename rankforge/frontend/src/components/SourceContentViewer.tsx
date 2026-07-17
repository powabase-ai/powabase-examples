"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";

import { Markdown } from "@/components/Markdown";
import type { SourceMeta, SourcePageMeta } from "@/lib/api";
import {
  useSourceMarkdown,
  useSourceMeta,
  useSourcePageBlob,
} from "@/lib/hooks/useResearch";

type Mode = "rendered" | "plain" | "pages";

/** Shows a source's extracted content in the display mode the user picks: rendered
 *  Markdown, plain Markdown, or — when the source has real page renders (uploaded
 *  PDFs) — the original page images (lazy-loaded). Used by Sources and Materials. */
export function SourceContentViewer({
  sourceId,
  emptyHint,
}: {
  sourceId: string | null;
  emptyHint?: string;
}) {
  const md = useSourceMarkdown(sourceId);
  const meta = useSourceMeta(sourceId);
  const hasPages = !!meta.data?.has_page_images;
  const [mode, setMode] = React.useState<Mode>("rendered");
  // If a source has no page renders, never show the pages mode (even if it was
  // left selected from a previous source).
  const effective: Mode = mode === "pages" && !hasPages ? "rendered" : mode;

  if (!sourceId) {
    return (
      <p className="text-sm text-muted-foreground">
        {emptyHint ?? "No extracted content for this source yet."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-xs text-muted-foreground">Display</label>
        <select
          value={effective}
          onChange={(e) => setMode(e.target.value as Mode)}
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
        >
          <option value="rendered">Rendered markdown</option>
          <option value="plain">Plain markdown</option>
          {hasPages && <option value="pages">Original pages</option>}
        </select>
        {(meta.isLoading || md.isLoading) && (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        )}
      </div>

      {effective === "pages" && meta.data ? (
        <SourcePageView sourceId={sourceId} meta={meta.data} />
      ) : md.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading content…</p>
      ) : md.error ? (
        <p className="text-sm text-destructive">
          {(md.error as Error).message}
        </p>
      ) : !md.data?.markdown ? (
        <p className="text-sm text-muted-foreground">
          No extracted content for this source yet.
        </p>
      ) : effective === "plain" ? (
        <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-3 font-data text-xs">
          {md.data.markdown}
        </pre>
      ) : (
        <Markdown>{md.data.markdown}</Markdown>
      )}
    </div>
  );
}

function SourcePageView({
  sourceId,
  meta,
}: {
  sourceId: string;
  meta: SourceMeta;
}) {
  return (
    <div className="space-y-4">
      <p className="text-[11px] text-muted-foreground">
        {meta.page_count} page{meta.page_count === 1 ? "" : "s"} · rendered from the
        original document
      </p>
      {meta.pages.map((p) => (
        <SourcePage key={p.index} sourceId={sourceId} page={p} />
      ))}
    </div>
  );
}

/** One page image, lazy-loaded: an IntersectionObserver (one-shot, 300px margin) gates
 *  the authed blob fetch; the div reserves the page's exact aspect ratio so scroll
 *  position is stable before the image arrives (judocu viewer pattern). */
function SourcePage({
  sourceId,
  page,
}: {
  sourceId: string;
  page: SourcePageMeta;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          obs.disconnect();
        }
      },
      { rootMargin: "300px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const blob = useSourcePageBlob(sourceId, page.index, visible);
  const [url, setUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!blob.data) return;
    const objUrl = URL.createObjectURL(blob.data);
    setUrl(objUrl);
    return () => URL.revokeObjectURL(objUrl);
  }, [blob.data]);

  const aspect =
    page.width && page.height ? page.width / page.height : 8.5 / 11;

  return (
    <div
      ref={ref}
      className="relative mx-auto w-full max-w-2xl overflow-hidden rounded border border-border bg-white shadow-sm"
      style={{ aspectRatio: String(aspect) }}
    >
      {url ? (
        // Authed blob → object URL; next/image can't consume a blob URL, so a plain
        // <img> is correct here.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={`Page ${page.page}`}
          className="absolute inset-0 h-full w-full object-contain"
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center bg-muted/40 text-xs text-muted-foreground">
          {blob.error ? "Couldn't load this page" : `Page ${page.page}…`}
        </div>
      )}
    </div>
  );
}
