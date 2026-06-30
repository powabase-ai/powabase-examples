"use client";

import * as React from "react";
import Link from "next/link";
import {
  Boxes,
  Check,
  Crown,
  ExternalLink,
  Link2,
  Loader2,
  Scissors,
  Search,
  Settings,
  Sparkles,
  Trash2,
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
  useGenerateLink,
  useIgnoreBrokenLink,
  useLinkSuggestions,
  useRemoveBrokenLink,
  useSuggestLinks,
  useUpdateArticle,
} from "@/lib/hooks/useArticles";
import { useBrand } from "@/lib/hooks/useBrands";
import { useCluster } from "@/lib/hooks/useClusters";
import { cn } from "@/lib/utils";

/** Editor panel: stage internal links to the brand's other published articles, then
 *  accept (insert + re-score) or dismiss each. Deterministic suggestions come from the
 *  backend; this is just the review surface. */
export function InternalLinksPanel({
  articleId,
  brandId,
  onLocate,
}: {
  articleId: string;
  brandId: string;
  /** Jump the article preview to where a given URL is linked. */
  onLocate?: (url: string) => void;
}) {
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const brand = useBrand(brandId);
  const article = useArticle(articleId);
  const cluster = useCluster(article.data?.cluster_id ?? null);
  const { data, isLoading } = useLinkSuggestions(articleId);
  const suggest = useSuggestLinks(articleId);
  const apply = useApplyLink(articleId);
  const generate = useGenerateLink(articleId);
  const dismiss = useDismissLink(articleId);
  const [actingId, setActingId] = React.useState<string | null>(null);

  const role = article.data?.cluster_role;

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

  function doGenerate(id: string) {
    setActingId(id);
    generate.mutate(id, {
      onSuccess: () => toast.success("Contextual link written & inserted"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not write a link"),
      onSettled: () => setActingId(null),
    });
  }

  const busy = apply.isPending || dismiss.isPending || generate.isPending;

  return (
    <div className="space-y-3">
      {article.data?.cluster_id && (
        <Link
          href={`/brands/${brandId}/clusters`}
          className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-secondary/50"
        >
          {role === "pillar" ? (
            <Crown className="size-3.5 shrink-0 text-[rgb(var(--gold))]" />
          ) : (
            <Boxes className="size-3.5 shrink-0 text-[rgb(var(--ember))]" />
          )}
          <span className="min-w-0 flex-1 truncate">
            {role === "pillar" ? "Pillar of" : "In cluster"}{" "}
            <span className="font-medium">{cluster.data?.label ?? "…"}</span>
          </span>
        </Link>
      )}

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
          {pending.map((s) => {
            const isGap = !s.anchor_text;
            const structural = s.kind === "pillar" || s.kind === "member";
            return (
              <li
                key={s.id}
                className={cn(
                  "rounded-md border p-2.5 text-sm",
                  structural
                    ? "border-[rgb(var(--gold))]/40 bg-[rgb(var(--gold))]/[0.04]"
                    : "border-border"
                )}
              >
                <div className="flex items-start gap-1.5">
                  {s.kind === "pillar" ? (
                    <Crown className="mt-0.5 size-3.5 shrink-0 text-[rgb(var(--gold))]" />
                  ) : s.kind === "member" ? (
                    <Boxes className="mt-0.5 size-3.5 shrink-0 text-[rgb(var(--ember))]" />
                  ) : (
                    <Link2 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0">
                    {structural && (
                      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                        {s.kind === "pillar"
                          ? "Link up to pillar"
                          : "Link down to member"}
                      </div>
                    )}
                    {isGap ? (
                      <div className="text-xs text-muted-foreground">
                        {s.reason}
                      </div>
                    ) : (
                      <div>
                        Link <span className="font-medium">“{s.anchor_text}”</span>
                      </div>
                    )}
                    {s.target_title && (
                      <div className="mt-0.5 truncate text-xs text-muted-foreground">
                        → {s.target_title}
                      </div>
                    )}
                  </div>
                </div>
                {canEdit && (
                  <div className="mt-2 flex gap-2">
                    {isGap ? (
                      <Button
                        variant="gold"
                        size="sm"
                        onClick={() => doGenerate(s.id)}
                        disabled={busy}
                      >
                        {generate.isPending && actingId === s.id ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Sparkles />
                        )}
                        Generate link
                      </Button>
                    ) : (
                      <Button
                        variant="gold"
                        size="sm"
                        onClick={() => doApply(s.id)}
                        disabled={busy}
                      >
                        {apply.isPending && actingId === s.id ? (
                          <Loader2 className="animate-spin" />
                        ) : (
                          <Check />
                        )}
                        Add
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => dismiss.mutate(s.id)}
                      disabled={busy}
                    >
                      <X />
                      Dismiss
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <BrokenLinksSection
        articleId={articleId}
        canEdit={canEdit}
        onLocate={onLocate}
      />
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
  onLocate,
}: {
  articleId: string;
  canEdit: boolean;
  onLocate?: (url: string) => void;
}) {
  const { data } = useBrokenLinks(articleId);
  const check = useCheckLinks(articleId);
  const ignore = useIgnoreBrokenLink(articleId);
  const remove = useRemoveBrokenLink(articleId);
  const [acting, setActing] = React.useState<{ id: string; keep: boolean } | null>(
    null
  );
  const open = (data ?? []).filter((b) => b.status === "open");
  const busy = ignore.isPending || remove.isPending;

  function doRemove(findingId: string, keepText: boolean) {
    if (
      !keepText &&
      !window.confirm(
        "Remove this link and let AI rephrase the sentence so it reads naturally? " +
          "(Use “Unlink” to just drop the link and keep the words as-is.)"
      )
    )
      return;
    setActing({ id: findingId, keep: keepText });
    remove.mutate(
      { findingId, keepText },
      {
        onSuccess: () =>
          toast.success(
            keepText ? "Unlinked — text kept" : "Removed — sentence rephrased"
          ),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Could not edit the link"),
        onSettled: () => setActing(null),
      }
    );
  }

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
          {open.map((b) => {
            const unlinking = acting?.id === b.id && acting.keep;
            const removing = acting?.id === b.id && !acting.keep;
            return (
              <li
                key={b.id}
                className="rounded-md border border-destructive/30 bg-destructive/[0.04] p-2.5 text-sm"
              >
                <button
                  type="button"
                  onClick={() => onLocate?.(b.url)}
                  className="flex w-full items-start gap-1.5 text-left"
                  title="Find this link in the article"
                >
                  <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium underline-offset-2 hover:underline">
                      {b.anchor_text || b.url}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {b.url}
                    </div>
                    <div className="mt-0.5 text-xs text-destructive">
                      {b.reason || "broken"} · click to find in article
                    </div>
                  </div>
                </button>
                {canEdit && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => doRemove(b.id, true)}
                      disabled={busy}
                      title="Drop the link but keep the words"
                    >
                      {unlinking ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <Scissors />
                      )}
                      Unlink
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => doRemove(b.id, false)}
                      disabled={busy}
                      title="Remove the link and let AI mend the sentence"
                    >
                      {removing ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <Trash2 />
                      )}
                      Remove
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => ignore.mutate(b.id)}
                      disabled={busy}
                      title="Leave it; stop flagging it"
                    >
                      <X />
                      Ignore
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
