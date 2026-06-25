"use client";

import * as React from "react";
import {
  Check,
  ExternalLink,
  Link2,
  Loader2,
  Search,
  Unlink,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove } from "@/lib/api";
import {
  useApplyLink,
  useBrokenLinks,
  useCheckLinks,
  useDismissLink,
  useIgnoreBrokenLink,
  useLinkSuggestions,
  useSuggestLinks,
} from "@/lib/hooks/useArticles";

/** Editor panel: stage internal links to the brand's other published articles, then
 *  accept (insert + re-score) or dismiss each. Deterministic suggestions come from the
 *  backend; this is just the review surface. */
export function InternalLinksPanel({ articleId }: { articleId: string }) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data, isLoading } = useLinkSuggestions(articleId);
  const suggest = useSuggestLinks(articleId);
  const apply = useApplyLink(articleId);
  const dismiss = useDismissLink(articleId);
  const [actingId, setActingId] = React.useState<string | null>(null);

  const pending = (data ?? []).filter((s) => s.status === "pending");

  function runSuggest() {
    suggest.mutate(undefined, {
      onSuccess: (rows) =>
        toast.success(
          rows.length
            ? `${rows.length} internal link${rows.length === 1 ? "" : "s"} found`
            : "No new internal links found"
        ),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not scan for links"),
    });
  }

  function doApply(id: string) {
    setActingId(id);
    apply.mutate(id, {
      onSuccess: () => toast.success("Link added — re-scoring"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not add link"),
      onSettled: () => setActingId(null),
    });
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Link this article to your other <strong>published</strong> articles where it
        already mentions them — internal links search engines and answer engines
        reward.
      </p>

      {canEdit && (
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={runSuggest}
          disabled={suggest.isPending}
        >
          {suggest.isPending ? (
            <Loader2 className="animate-spin" />
          ) : (
            <Search />
          )}
          Find internal links
        </Button>
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : pending.length === 0 ? (
        <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
          No internal-link suggestions.{" "}
          {canEdit
            ? "Run a scan once you have other published articles."
            : "An editor can scan for links."}
        </div>
      ) : (
        <ul className="space-y-2">
          {pending.map((s) => (
            <li
              key={s.id}
              className="rounded-md border border-border p-2.5 text-sm"
            >
              <div className="flex items-start gap-1.5">
                <Link2 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div>
                    Link <span className="font-medium">“{s.anchor_text}”</span>
                  </div>
                  {s.target_title && (
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      → {s.target_title}
                    </div>
                  )}
                </div>
              </div>
              {canEdit && (
                <div className="mt-2 flex gap-2">
                  <Button
                    variant="gold"
                    size="sm"
                    onClick={() => doApply(s.id)}
                    disabled={apply.isPending || dismiss.isPending}
                  >
                    {apply.isPending && actingId === s.id ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <Check />
                    )}
                    Add
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => dismiss.mutate(s.id)}
                    disabled={apply.isPending || dismiss.isPending}
                  >
                    <X />
                    Dismiss
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <BrokenLinksSection articleId={articleId} canEdit={canEdit} />
    </div>
  );
}

/** Validate this article's outbound links (internal targets + external URLs) and let
 *  an editor fix the prose or ignore a finding. */
function BrokenLinksSection({
  articleId,
  canEdit,
}: {
  articleId: string;
  canEdit: boolean;
}) {
  const { data } = useBrokenLinks(articleId);
  const check = useCheckLinks(articleId);
  const ignore = useIgnoreBrokenLink(articleId);
  const open = (data ?? []).filter((b) => b.status === "open");

  return (
    <div className="space-y-2 border-t border-border pt-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Broken links
        </h4>
        {canEdit && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              check.mutate(undefined, {
                onSuccess: (rows) =>
                  toast.success(
                    rows.length
                      ? `${rows.length} broken link${rows.length === 1 ? "" : "s"}`
                      : "No broken links"
                  ),
                onError: (e) =>
                  toast.error(e instanceof Error ? e.message : "Check failed"),
              })
            }
            disabled={check.isPending}
          >
            {check.isPending ? <Loader2 className="animate-spin" /> : <Unlink />}
            Check
          </Button>
        )}
      </div>
      {open.length === 0 ? (
        <p className="text-xs text-muted-foreground">No broken links found.</p>
      ) : (
        <ul className="space-y-2">
          {open.map((b) => (
            <li
              key={b.id}
              className="rounded-md border border-destructive/30 bg-destructive/[0.04] p-2.5 text-sm"
            >
              <div className="flex items-start gap-1.5">
                <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">
                    {b.anchor_text || b.url}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {b.url}
                  </div>
                  <div className="mt-0.5 text-xs text-destructive">
                    {b.reason || "broken"}
                  </div>
                </div>
              </div>
              {canEdit && (
                <div className="mt-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => ignore.mutate(b.id)}
                    disabled={ignore.isPending}
                  >
                    <X />
                    Ignore
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
