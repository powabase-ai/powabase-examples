import { useState } from "react";

import type { Article } from "@/lib/api";

/** Are the last run's results (the before/after score strip, frontmatter flags)
 *  still about the article as it is now?
 *
 *  Rule: remember `updated_at` at the moment this article is first seen settled
 *  (`generation_status === "done"`, on mount or when a run finishes); the results
 *  are current only while `updated_at` still equals that stamp. Any later write — a
 *  manual edit, a frontmatter save, "Generate summary & FAQ", a status change —
 *  bumps `updated_at` and hides them. (A revert also clears `progress.before`
 *  server-side.) After a page reload the stamp restarts from the loaded record, so
 *  results reappear until the next write — acceptable for an advisory display.
 *  Call it from the always-mounted article page (not a tab panel), so switching
 *  tabs doesn't restart the stamp. */
export function useRunCurrent(
  article: Pick<Article, "id" | "generation_status" | "updated_at"> | undefined
): boolean {
  const [stamp, setStamp] = useState<{ id: string; at: string } | null>(null);
  const settled = article?.generation_status === "done";
  // Adjust state during render (React's "storing information from previous
  // renders" pattern) so the first settled render already has the stamp.
  if (!article || !settled) {
    if (stamp !== null) setStamp(null);
    return false;
  } else if (stamp === null || stamp.id !== article.id) {
    setStamp({ id: article.id, at: article.updated_at });
  }
  if (stamp === null || stamp.id !== article.id) return true; // stamp set this render
  return stamp.at === article.updated_at;
}
