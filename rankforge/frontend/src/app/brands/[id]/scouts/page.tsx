"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  ExternalLink,
  Loader2,
  PenLine,
  Radar,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  useDismissOpportunity,
  useDraftOpportunity,
  useOpportunities,
  useRestoreOpportunity,
  useRunScout,
  useScoutConfig,
  useScoutRuns,
  useUpdateScoutConfig,
} from "@/lib/hooks/useScouts";
import { useAuth } from "@/lib/auth/AuthProvider";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import { canApprove, type Opportunity, type ScoutConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

function scoreColor(s: number) {
  if (s >= 70) return "var(--success)";
  if (s >= 50) return "var(--ember)";
  return "var(--muted-ink)";
}

function ConfigPanel({
  brandId,
  config,
  canEdit,
}: {
  brandId: string;
  config: ScoutConfig;
  canEdit: boolean;
}) {
  const update = useUpdateScoutConfig(brandId);
  const [form, setForm] = useState<ScoutConfig>(config);
  useEffect(() => setForm(config), [config]);

  function save() {
    update.mutate(
      {
        enabled: form.enabled,
        cadence: form.cadence,
        autonomy: form.autonomy,
        min_score: form.min_score,
        max_drafts_per_run: form.max_drafts_per_run,
      },
      {
        onSuccess: () => toast.success("Scout settings saved"),
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Save failed"),
      }
    );
  }

  const field =
    "h-9 rounded-md border border-input bg-card px-2 text-sm outline-none focus:ring-1 focus:ring-[rgb(var(--ember))] disabled:opacity-60";

  return (
    <Card>
      <CardContent className="grid gap-4 py-5 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <label className="text-xs font-medium text-muted-foreground">
            Auto-scout frequency
          </label>
          <select
            value={form.enabled ? form.cadence : "off"}
            disabled={!canEdit}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "off") setForm({ ...form, enabled: false });
              else
                setForm({
                  ...form,
                  enabled: true,
                  cadence: v as ScoutConfig["cadence"],
                });
            }}
            className={cn(field, "w-full")}
          >
            <option value="off">Off — manual only</option>
            <option value="twice_daily">Twice a day</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
          <p className="text-xs text-muted-foreground">
            {form.enabled
              ? "Scouts run automatically on this schedule. You can still run one anytime with “Run now.”"
              : "No automatic scouting — use “Run now” to scout on demand."}
          </p>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            Autonomy
          </label>
          <select
            value={form.autonomy}
            disabled={!canEdit}
            onChange={(e) =>
              setForm({
                ...form,
                autonomy: e.target.value as ScoutConfig["autonomy"],
              })
            }
            className={cn(field, "w-full")}
          >
            <option value="suggest">Suggest only (inbox)</option>
            <option value="auto_draft">Auto-draft top picks → in review</option>
          </select>
        </div>

        {form.autonomy === "auto_draft" && (
          <>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Min score to auto-draft
              </label>
              <input
                type="number"
                min={0}
                max={100}
                value={form.min_score}
                disabled={!canEdit}
                onChange={(e) =>
                  setForm({ ...form, min_score: Number(e.target.value) })
                }
                className={cn(field, "w-full")}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">
                Max drafts per run
              </label>
              <input
                type="number"
                min={1}
                max={5}
                value={form.max_drafts_per_run}
                disabled={!canEdit}
                onChange={(e) =>
                  setForm({
                    ...form,
                    max_drafts_per_run: Number(e.target.value),
                  })
                }
                className={cn(field, "w-full")}
              />
            </div>
          </>
        )}

        {canEdit && (
          <div className="sm:col-span-2">
            <Button
              variant="outline"
              size="sm"
              onClick={save}
              disabled={update.isPending}
            >
              {update.isPending && <Loader2 className="animate-spin" />}
              Save settings
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OpportunityCard({
  brandId,
  opp,
}: {
  brandId: string;
  opp: Opportunity;
}) {
  const draft = useDraftOpportunity(brandId);
  const dismiss = useDismissOpportunity(brandId);
  const restore = useRestoreOpportunity(brandId);
  const busy = opp.status === "queued" || opp.status === "drafting";

  return (
    <Card className={cn(opp.status === "dismissed" && "opacity-50")}>
      <CardContent className="flex gap-4 py-4">
        <div
          className="flex size-10 shrink-0 flex-col items-center justify-center rounded-md font-data text-sm font-semibold"
          style={{
            color: `rgb(${scoreColor(opp.score)})`,
            background: `rgb(${scoreColor(opp.score)} / 0.12)`,
          }}
        >
          {opp.score}
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium leading-snug">{opp.title}</div>
          {opp.why_now && (
            <p className="mt-1 text-xs italic text-muted-foreground">
              {opp.why_now}
            </p>
          )}
          {opp.angle && <p className="mt-1.5 text-sm">{opp.angle}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {opp.source_type && (
              <span className="rounded bg-secondary px-1.5 py-0.5 capitalize">
                {opp.source_type}
              </span>
            )}
            {opp.keyword && <span className="font-data">{opp.keyword}</span>}
            {opp.source_url && (
              <a
                href={opp.source_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 hover:text-foreground"
              >
                <ExternalLink className="size-3" /> source
              </a>
            )}
          </div>

          <div className="mt-3 flex items-center gap-2">
            {opp.status === "drafted" && opp.article_id ? (
              <Link href={`/brands/${brandId}/articles/${opp.article_id}`}>
                <Button variant="gold" size="sm">
                  <PenLine /> View draft
                </Button>
              </Link>
            ) : busy ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-[rgb(var(--ember-bright))]">
                <Loader2 className="size-3.5 animate-spin" /> Drafting…
              </span>
            ) : opp.status === "new" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => draft.mutate(opp.id)}
                disabled={draft.isPending}
              >
                <Sparkles /> Draft this
              </Button>
            ) : null}

            {opp.status !== "dismissed" && !busy && (
              <button
                onClick={() => dismiss.mutate(opp.id)}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
              >
                <Trash2 className="size-3" /> Dismiss
              </button>
            )}

            {opp.status === "dismissed" && (
              <button
                onClick={() => restore.mutate(opp.id)}
                disabled={restore.isPending}
                className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                <RotateCcw className="size-3" /> Restore
              </button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ScoutsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { profile } = useAuth();
  const canEdit = canApprove(profile?.role);
  const config = useScoutConfig(id);
  const runs = useScoutRuns(id);
  const opps = useOpportunities(id);
  const runScout = useRunScout(id);

  const lastRun = runs.data?.[0];
  const active = opps.data?.filter((o) => o.status !== "dismissed") ?? [];
  const dismissed = opps.data?.filter((o) => o.status === "dismissed") ?? [];

  function run() {
    runScout.mutate(undefined, {
      onSuccess: () => toast.success("Scout started — discovering opportunities…"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start scout"),
    });
  }

  return (
    <Page>
      <PageHeader
        icon={Radar}
        title="Scouts"
        actions={
          canEdit && (
            <Button
              variant="gold"
              size="sm"
              onClick={run}
              disabled={runScout.isPending || lastRun?.status === "running"}
            >
              {runScout.isPending || lastRun?.status === "running" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Radar />
              )}
              Run now
            </Button>
          )
        }
      />
      <PageBody>
      <p className="mb-5 text-sm text-muted-foreground">
        Scouts watch the market for timely, on-brand topics and surface scored
        opportunities here. At the auto-draft level they push the best ones through
        the pipeline and stage them as <span className="font-medium">in review</span>.
      </p>

      {lastRun?.status === "running" ? (
        <div className="mb-4 flex items-start gap-2.5 rounded-md border border-[rgb(var(--ember))]/30 bg-[rgb(var(--ember))]/[0.06] px-3 py-2.5">
          <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-[rgb(var(--ember-bright))]" />
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">
              {lastRun.progress?.message ?? "Scouting…"}
            </div>
            {lastRun.progress?.considered &&
              lastRun.progress.considered.length > 0 && (
                <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  Considering: {lastRun.progress.considered.join(" · ")}
                </div>
              )}
          </div>
        </div>
      ) : lastRun ? (
        <p className="mb-4 text-xs text-muted-foreground">
          Last run:{" "}
          <span className="capitalize">{lastRun.status}</span> ·{" "}
          {lastRun.found} found · {lastRun.drafted} drafted
          {lastRun.error ? ` · ${lastRun.error}` : ""}
        </p>
      ) : null}

      {config.data && (
        <div className="mb-6">
          <ConfigPanel brandId={id} config={config.data} canEdit={canEdit} />
        </div>
      )}

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Opportunity inbox
      </h2>

      {opps.isLoading && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {opps.data && active.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No opportunities yet.{" "}
            {canEdit ? "Run a scout to discover some." : "Check back soon."}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-3">
        {active.map((o) => (
          <OpportunityCard key={o.id} brandId={id} opp={o} />
        ))}
      </div>

      {dismissed.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Dismissed ({dismissed.length})
          </summary>
          <div className="mt-3 grid gap-3">
            {dismissed.map((o) => (
              <OpportunityCard key={o.id} brandId={id} opp={o} />
            ))}
          </div>
        </details>
      )}
      </PageBody>
    </Page>
  );
}
