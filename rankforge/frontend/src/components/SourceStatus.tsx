import { Loader2 } from "lucide-react";

/** Terminal scrape states that left no usable content — the only ones worth re-scraping.
 *  Excludes non-terminal states (`retrying`, `extracting`) and success/unknown. The
 *  backend now persists a poll-timeout as `failed` (not `extracting`), so a stuck row is
 *  retryable rather than an eternal spinner. NULL/absent (legacy) is not retryable. */
const TERMINAL_FAILURES = new Set(["failed", "attention_required", "cancelled"]);

export const isRetryable = (status?: string | null): boolean =>
  !!status && TERMINAL_FAILURES.has(status);

/** Scrape state chip for a source row. 'extracted' renders nothing (word-count + trust
 *  badge already convey success). A KNOWN terminal failure is red; everything else
 *  non-extracted (`retrying`, `extracting`, or any status we don't recognize) is a neutral
 *  spinner — so an in-flight or unknown state is never mislabeled as a hard failure. */
export function StatusChip({ status }: { status?: string | null }) {
  if (!status || status === "extracted") return null;
  if (TERMINAL_FAILURES.has(status))
    return (
      <span className="inline-flex items-center gap-1 rounded bg-destructive/15 px-1.5 py-0.5 text-destructive">
        failed
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded bg-[rgb(var(--gold))]/15 px-1.5 py-0.5 text-[rgb(var(--accent-gold-hover))]">
      <Loader2 className="size-3 animate-spin" /> {status}
    </span>
  );
}
