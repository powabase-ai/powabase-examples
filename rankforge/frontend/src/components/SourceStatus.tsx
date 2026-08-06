import { Loader2 } from "lucide-react";

/** A source row is retryable when its scrape did NOT leave usable content and it isn't
 *  already in flight. A NULL/absent status (legacy rows) is treated as not retryable. */
export const isRetryable = (status?: string | null): boolean =>
  !!status && status !== "extracted" && status !== "retrying";

/** Scrape state chip for a source row. 'extracted' renders nothing (word-count + trust
 *  badge already convey success); only the not-yet-usable states show a chip. */
export function StatusChip({ status }: { status?: string | null }) {
  if (!status || status === "extracted") return null;
  if (status === "retrying")
    return (
      <span className="inline-flex items-center gap-1 rounded bg-[rgb(var(--gold))]/15 px-1.5 py-0.5 text-[rgb(var(--accent-gold-hover))]">
        <Loader2 className="size-3 animate-spin" /> retrying
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded bg-destructive/15 px-1.5 py-0.5 text-destructive">
      failed
    </span>
  );
}
