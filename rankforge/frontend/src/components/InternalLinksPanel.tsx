"use client";

import * as React from "react";
import Link from "next/link";
import {
  Check,
  ExternalLink,
  Link2,
  Loader2,
  Search,
  Settings,
  Unlink,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove, type BusinessProfile } from "@/lib/api";
import {
  useApplyLink,
  useArticle,
  useBrokenLinks,
  useCheckLinks,
  useDismissLink,
  useIgnoreBrokenLink,
  useLinkSuggestions,
  useSuggestLinks,
  useUpdateArticle,
} from "@/lib/hooks/useArticles";
import { useBrand } from "@/lib/hooks/useBrands";

/** Editor panel: stage internal links to the brand's other published articles, then
 *  accept (insert + re-score) or dismiss each. Deterministic suggestions come from the
 *  backend; this is just the review surface. */
export function InternalLinksPanel({
  articleId,
  brandId,
}: {
  articleId: string;
  brandId: string;
}) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const brand = useBrand(brandId);
  const { data, isLoading } = useLinkSuggestions(articleId);
  const suggest = useSuggestLinks(articleId);
  const apply = useApplyLink(articleId);
  const dismiss = useDismissLink(articleId);
  const [actingId, setActingId] = React.useState<string | null>(null);

  const hasPattern = !!brand.data?.url_pattern;
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

      <CanonicalUrlField articleId={articleId} brand={brand.data} />

      {brand.data && !hasPattern ? (
        <div className="rounded-md border border-[rgb(var(--ember))]/30 bg-[rgb(var(--ember))]/[0.06] px-3 py-2.5 text-xs">
          Set your{" "}
          <Link
            href={`/brands/${brandId}/settings`}
            className="inline-flex items-center gap-0.5 font-medium text-[rgb(var(--ember))] hover:underline"
          >
            <Settings className="size-3" /> blog URL pattern
          </Link>{" "}
          to enable internal links — we need to know where your published articles
          live before we can point links at them.
        </div>
      ) : (
        canEdit && (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={runSuggest}
            disabled={suggest.isPending || !hasPattern}
          >
            {suggest.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Search />
            )}
            Find internal links
          </Button>
        )
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

/** This article's published URL: an explicit override (wins over the brand pattern),
 *  with the pattern-resolved URL shown as the default. Editors only. */
function CanonicalUrlField({
  articleId,
  brand,
}: {
  articleId: string;
  brand?: BusinessProfile;
}) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data: article } = useArticle(articleId);
  const update = useUpdateArticle(articleId);
  const [value, setValue] = React.useState("");
  React.useEffect(
    () => setValue(article?.canonical_url ?? ""),
    [article?.canonical_url]
  );

  const computed =
    brand?.url_pattern && article?.slug
      ? brand.url_pattern
          .replace("{slug}", article.slug)
          .replace("{id}", article.id)
      : null;

  if (!canEdit) return null;
  const dirty = value.trim() !== (article?.canonical_url ?? "");

  return (
    <div className="space-y-1.5 rounded-md border border-border p-2.5">
      <label className="text-xs font-medium text-muted-foreground">
        This article&apos;s published URL
      </label>
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={computed ?? "https://blog.acme.com/my-article"}
          className="h-8 text-xs"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            update.mutate(
              { canonical_url: value.trim() },
              {
                onSuccess: () => toast.success("Published URL saved"),
                onError: (e) =>
                  toast.error(e instanceof Error ? e.message : "Save failed"),
              }
            )
          }
          disabled={!dirty || update.isPending}
        >
          {update.isPending && <Loader2 className="animate-spin" />}
          Save
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        {value.trim() ? (
          "Override — internal links point here."
        ) : computed ? (
          <>
            From your pattern: <span className="font-data">{computed}</span>
          </>
        ) : (
          "Set a blog URL pattern in settings, or enter an explicit URL."
        )}
      </p>
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
