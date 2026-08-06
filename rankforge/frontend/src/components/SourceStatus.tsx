import { Loader2 } from "lucide-react";

/** Terminal scrape states that left no usable content — the only ones worth re-scraping.
 *  Deliberately excludes non-terminal states (`retrying`, `extracting`): re-importing a
 *  page whose extraction is still running wastes credits and can delete a Source that was
 *  seconds from finishing. NULL/absent (legacy rows) is also not retryable. */
const RETRYABLE_STATUSES = new Set(["failed", "attention_required", "cancelled"]);

export const isRetryable = (status?: string | null): boolean =>
  !!status && RETRYABLE_STATUSES.has(status);

/** Scrape state chip for a source row. 'extracted' renders nothing (word-count + trust
 *  badge already convey success). In-flight states (`retrying`, or `extracting` while the
 *  upstream extraction is still running) show a neutral spinner — not a red failure —
 *  so a still-working source isn't mislabeled. Everything else is a terminal failure. */
export function StatusChip({ status }: { status?: string | null }) {
  if (!status || status === "extracted") return null;
  if (status === "retrying" || status === "extracting")
    return (
      <span className="inline-flex items-center gap-1 rounded bg-[rgb(var(--gold))]/15 px-1.5 py-0.5 text-[rgb(var(--accent-gold-hover))]">
        <Loader2 className="size-3 animate-spin" /> {status}
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded bg-destructive/15 px-1.5 py-0.5 text-destructive">
      failed
    </span>
  );
}
