"use client";

import { use, useState } from "react";
import Link from "next/link";
import {
  Boxes,
  ChevronDown,
  Crown,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import {
  useAnalyzeGaps,
  useBackfillClusters,
  useCluster,
  useClusters,
  useSetPillar,
} from "@/lib/hooks/useClusters";
import { useAuth } from "@/lib/auth/AuthProvider";
import { canApprove, type ContentCluster } from "@/lib/api";
import { cn } from "@/lib/utils";

function ClusterCard({
  brandId,
  cluster,
  canEdit,
}: {
  brandId: string;
  cluster: ContentCluster;
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(false);
  const detail = useCluster(open ? cluster.id : null);
  const setPillar = useSetPillar(cluster.id);
  const analyzeGaps = useAnalyzeGaps();

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
                  </li>
                ))}
                {(detail.data?.members ?? []).length === 0 && (
                  <li className="px-2 text-sm text-muted-foreground">
                    No articles in this cluster yet.
                  </li>
                )}
              </ul>
            )}
            {canEdit && cluster.pillar_article_id && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-2"
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
          </div>
        )}
      </CardContent>
    </Card>
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

  return (
    <Page>
      <PageHeader
        icon={Boxes}
        title="Content clusters"
        actions={
          canEdit && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                backfill.mutate(undefined, {
                  onSuccess: () =>
                    toast.success("Clustering unassigned articles…"),
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
              articles{canEdit ? " — or hit Backfill to cluster existing ones." : "."}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {data?.map((c) => (
              <ClusterCard
                key={c.id}
                brandId={id}
                cluster={c}
                canEdit={canEdit}
              />
            ))}
          </div>
        )}
      </PageBody>
    </Page>
  );
}
