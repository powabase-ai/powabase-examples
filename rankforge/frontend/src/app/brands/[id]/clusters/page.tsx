"use client";

import { use, useState } from "react";
import Link from "next/link";
import * as Select from "@radix-ui/react-select";
import {
  Boxes,
  ChevronDown,
  Crown,
  FolderInput,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import {
  useAnalyzeGaps,
  useBackfillClusters,
  useCluster,
  useClusters,
  useCreateCluster,
  useDeleteCluster,
  useMoveArticle,
  useSetPillar,
} from "@/lib/hooks/useClusters";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove, type ContentCluster } from "@/lib/api";
import { cn } from "@/lib/utils";

/** A compact "move this article to another cluster" picker (Radix Select used as an
 *  action menu). Lists every cluster except the one the article is already in. */
function MoveMenu({
  brandId,
  currentClusterId,
  articleId,
  clusters,
}: {
  brandId: string;
  currentClusterId: string;
  articleId: string;
  clusters: ContentCluster[];
}) {
  const move = useMoveArticle(brandId);
  const targets = clusters.filter((c) => c.id !== currentClusterId);
  if (targets.length === 0) return null;

  return (
    <Select.Root
      value=""
      onValueChange={(toClusterId) =>
        move.mutate(
          { toClusterId, articleId },
          {
            onSuccess: () => toast.success("Article moved"),
            onError: (e) =>
              toast.error(e instanceof Error ? e.message : "Move failed"),
          }
        )
      }
      disabled={move.isPending}
    >
      <Select.Trigger
        aria-label="Move to another cluster"
        title="Move to another cluster"
        className="inline-flex shrink-0 items-center gap-1 rounded px-1 text-xs text-muted-foreground outline-none hover:text-foreground data-[state=open]:text-foreground disabled:opacity-50"
      >
        {move.isPending ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <FolderInput className="size-3.5" />
        )}
        Move
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          position="popper"
          sideOffset={4}
          align="end"
          className="z-50 max-h-[50vh] w-56 overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        >
          <Select.Viewport>
            <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Move to cluster
            </div>
            {targets.map((c) => (
              <Select.Item
                key={c.id}
                value={c.id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-secondary"
              >
                <Boxes className="size-3.5 shrink-0 text-[rgb(var(--ember))]" />
                <Select.ItemText>{c.label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

function ClusterCard({
  brandId,
  cluster,
  clusters,
  canEdit,
}: {
  brandId: string;
  cluster: ContentCluster;
  clusters: ContentCluster[];
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(false);
  const detail = useCluster(open ? cluster.id : null);
  const setPillar = useSetPillar(cluster.id);
  const analyzeGaps = useAnalyzeGaps();
  const del = useDeleteCluster(brandId);

  function onDelete() {
    if (
      !window.confirm(
        `Delete the “${cluster.label}” cluster? Its ${cluster.member_count} member` +
          `${cluster.member_count === 1 ? "" : "s"} stay as articles but become ` +
          "unclustered (re-cluster them anytime with Backfill)."
      )
    )
      return;
    del.mutate(cluster.id, {
      onSuccess: () => toast.success("Cluster deleted"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
    });
  }

  return (
    <Card>
      <CardContent className="py-4">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-start gap-3 text-left"
        >
          <ChevronDown
            className={cn(
              "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Boxes className="size-4 shrink-0 text-[rgb(var(--ember))]" />
              <span className="truncate font-medium">{cluster.label}</span>
              <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">
                {cluster.member_count} member
                {cluster.member_count === 1 ? "" : "s"}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Crown className="size-3 shrink-0 text-[rgb(var(--gold))]" />
              Pillar:{" "}
              <span className="truncate font-medium text-foreground">
                {cluster.pillar_title ?? "— not written yet —"}
              </span>
            </div>
            {cluster.theme && (
              <p className="mt-1.5 line-clamp-2 text-sm text-muted-foreground">
                {cluster.theme}
              </p>
            )}
          </div>
        </button>

        {open && (
          <div className="mt-3 border-t border-border pt-3">
            {detail.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading members…</p>
            ) : (
              <ul className="space-y-1.5">
                {(detail.data?.members ?? []).map((m) => (
                  <li
                    key={m.id}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-secondary/50"
                  >
                    {m.cluster_role === "pillar" ? (
                      <Crown className="size-3.5 shrink-0 text-[rgb(var(--gold))]" />
                    ) : (
                      <span className="ml-1 size-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
                    )}
                    <Link
                      href={`/brands/${brandId}/articles/${m.id}`}
                      className="min-w-0 flex-1 truncate hover:underline"
                    >
                      {m.title}
                    </Link>
                    <span className="shrink-0 text-xs text-muted-foreground capitalize">
                      {m.status}
                    </span>
                    {canEdit && m.cluster_role !== "pillar" && (
                      <button
                        type="button"
                        onClick={() =>
                          setPillar.mutate(m.id, {
                            onSuccess: () => toast.success("Pillar updated"),
                            onError: (e) =>
                              toast.error(
                                e instanceof Error ? e.message : "Failed"
                              ),
                          })
                        }
                        disabled={setPillar.isPending}
                        className="shrink-0 text-xs text-muted-foreground hover:text-[rgb(var(--gold))]"
                        title="Make this the cluster's pillar"
                      >
                        Make pillar
                      </button>
                    )}
                    {canEdit && (
                      <MoveMenu
                        brandId={brandId}
                        currentClusterId={cluster.id}
                        articleId={m.id}
                        clusters={clusters}
                      />
                    )}
                  </li>
                ))}
                {(detail.data?.members ?? []).length === 0 && (
                  <li className="px-2 text-sm text-muted-foreground">
                    No articles in this cluster yet.
                  </li>
                )}
              </ul>
            )}
            {canEdit && (
              <div className="mt-2 flex items-center gap-1">
                {cluster.pillar_article_id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      analyzeGaps.mutate(cluster.id, {
                        onSuccess: (r) =>
                          toast.success(
                            r.created
                              ? `${r.created} gap opportunit${
                                  r.created === 1 ? "y" : "ies"
                                } added to Scouts`
                              : "No new coverage gaps found"
                          ),
                        onError: (e) =>
                          toast.error(e instanceof Error ? e.message : "Failed"),
                      })
                    }
                    disabled={analyzeGaps.isPending}
                    title="Suggest articles for pillar subtopics not yet covered"
                  >
                    {analyzeGaps.isPending ? (
                      <Loader2 className="animate-spin" />
                    ) : (
                      <Sparkles />
                    )}
                    Find coverage gaps
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto text-muted-foreground hover:text-destructive"
                  onClick={onDelete}
                  disabled={del.isPending}
                  title="Delete this cluster"
                >
                  {del.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Trash2 />
                  )}
                  Delete
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Found a new empty cluster (label + theme). It starts pillar-less; the user then
 *  moves articles in and designates a pillar. */
function NewClusterDialog({
  brandId,
  open,
  onOpenChange,
}: {
  brandId: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const create = useCreateCluster(brandId);
  const [label, setLabel] = useState("");
  const [theme, setTheme] = useState("");

  function reset() {
    setLabel("");
    setTheme("");
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim()) return;
    create.mutate(
      { label: label.trim(), theme: theme.trim() || undefined },
      {
        onSuccess: () => {
          toast.success("Cluster created");
          reset();
          onOpenChange(false);
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "Create failed"),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="font-display">New cluster</DialogTitle>
          <DialogDescription>
            A cluster groups articles around one theme with a single authority pillar.
            It starts empty — move articles in and make one the pillar.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="cluster-label">Label</Label>
            <Input
              id="cluster-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Authentication"
              maxLength={120}
              autoFocus
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="cluster-theme">
              Theme <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="cluster-theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              placeholder="Which subtopics belong in this cluster — used to match future articles to it."
              rows={3}
              maxLength={2000}
            />
          </div>
          <DialogFooter className="mt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="gold"
              disabled={create.isPending || !label.trim()}
            >
              {create.isPending ? "Creating…" : "Create cluster"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ClustersPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const { data, isLoading } = useClusters(id);
  const backfill = useBackfillClusters(id);
  const [newOpen, setNewOpen] = useState(false);

  return (
    <Page>
      <PageHeader
        icon={Boxes}
        title="Content clusters"
        actions={
          canEdit && (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  backfill.mutate(undefined, {
                    onSuccess: ({ assigned, remaining }) =>
                      toast.success(
                        assigned > 0
                          ? `Clustered ${assigned} article${assigned === 1 ? "" : "s"}` +
                            (remaining ? " — more remain, run again" : "")
                          : "All articles are already clustered"
                      ),
                    onError: (e) =>
                      toast.error(e instanceof Error ? e.message : "Failed"),
                  })
                }
                disabled={backfill.isPending}
              >
                {backfill.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <RefreshCw />
                )}
                Backfill
              </Button>
              <Button variant="gold" size="sm" onClick={() => setNewOpen(true)}>
                <Plus /> New cluster
              </Button>
            </div>
          )
        }
      />
      <PageBody>
        <p className="mb-5 text-sm text-muted-foreground">
          Each topic cluster has one authoritative{" "}
          <span className="inline-flex items-center gap-0.5 font-medium">
            <Crown className="size-3 text-[rgb(var(--gold))]" /> pillar
          </span>{" "}
          article that supporting articles link up to — the structure search and
          answer engines reward. New articles are placed into a cluster
          automatically.
        </p>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (data?.length ?? 0) === 0 ? (
          <Card className="border-dashed">
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              No clusters yet. They form as you scout opportunities and publish
              articles{canEdit ? " — or hit Backfill to cluster existing ones, or " : "."}
              {canEdit && (
                <button
                  type="button"
                  onClick={() => setNewOpen(true)}
                  className="font-medium text-foreground underline underline-offset-2 hover:text-[rgb(var(--ember))]"
                >
                  create one manually
                </button>
              )}
              {canEdit && "."}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {data?.map((c) => (
              <ClusterCard
                key={c.id}
                brandId={id}
                cluster={c}
                clusters={data}
                canEdit={canEdit}
              />
            ))}
          </div>
        )}
      </PageBody>

      <NewClusterDialog brandId={id} open={newOpen} onOpenChange={setNewOpen} />
    </Page>
  );
}
