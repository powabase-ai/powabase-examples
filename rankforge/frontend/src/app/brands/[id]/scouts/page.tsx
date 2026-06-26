"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  Boxes,
  Crown,
  ExternalLink,
  Link2,
  Loader2,
  PenLine,
  Plus,
  Radar,
  RotateCcw,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  useDeleteOpportunity,
  useDismissOpportunity,
  useDraftOpportunity,
  useExecuteRun,
  useOpportunities,
  usePlanScout,
  useRelinkConfig,
  useRestoreOpportunity,
  useRunRelink,
  useRunScout,
  useScoutConfig,
  useScoutRun,
  useScoutRuns,
  useUpdatePlan,
  useUpdateRelink,
  useUpdateScoutConfig,
} from "@/lib/hooks/useScouts";
import { useClusters } from "@/lib/hooks/useClusters";
import { useAuth } from "@/lib/auth/AuthProvider";
import { Page, PageBody, PageHeader } from "@/components/layout/PageHeader";
import {
  canApprove,
  type Opportunity,
  type PlanSource,
  type RelinkConfig,
  type ScoutConfig,
  type ScoutPlan,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const PLAN_SOURCES: { value: PlanSource; label: string }[] = [
  { value: "news", label: "News" },
  { value: "youtube", label: "YouTube" },
  { value: "social", label: "Social" },
  { value: "web", label: "Web" },
];

/** The reviewable Search Plan: shows the trending queries the scout will run and lets
 *  the user add/remove/reword them and switch sources before executing. */
function SearchPlanPanel({
  brandId,
  runId,
  onClose,
}: {
  brandId: string;
  runId: string;
  onClose: () => void;
}) {
  const run = useScoutRun(runId);
  const updatePlan = useUpdatePlan(runId);
  const execute = useExecuteRun(brandId);
  const [draft, setDraft] = useState<ScoutPlan | null>(null);

  // Seed the editable draft from the generated plan once it lands.
  useEffect(() => {
    if (run.data?.plan && draft === null) setDraft(run.data.plan);
  }, [run.data?.plan, draft]);

  const r = run.data;
  const planning = !r || (r.status === "planned" && !r.plan);
  const failed = r?.status === "failed";
  const busy = updatePlan.isPending || execute.isPending;

  function patchQuery(i: number, patch: Partial<ScoutPlan["queries"][number]>) {
    setDraft((d) =>
      d ? { ...d, queries: d.queries.map((q, j) => (j === i ? { ...q, ...patch } : q)) } : d
    );
  }
  function addQuery() {
    setDraft((d) =>
      d ? { ...d, queries: [...d.queries, { query: "", source: "web", rationale: "" }] } : d
    );
  }
  function removeQuery(i: number) {
    setDraft((d) => (d ? { ...d, queries: d.queries.filter((_, j) => j !== i) } : d));
  }

  async function runIt() {
    if (!draft) return;
    const queries = draft.queries.filter((q) => q.query.trim());
    if (queries.length === 0) {
      toast.error("Add at least one search query.");
      return;
    }
    try {
      await updatePlan.mutateAsync({ ...draft, queries });
      await execute.mutateAsync(runId);
      toast.success("Scout is running your plan…");
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't start the run");
    }
  }

  return (
    <Card className="mb-4 border-[rgb(var(--ember))]/30">
      <CardContent className="py-4">
        <div className="flex items-center gap-2">
          <Wand2 className="size-4 text-[rgb(var(--ember))]" />
          <h3 className="text-sm font-semibold">Search plan</h3>
          <button
            onClick={onClose}
            className="ml-auto text-muted-foreground hover:text-foreground"
            title="Discard this plan"
          >
            <X className="size-4" />
          </button>
        </div>

        {planning ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin text-[rgb(var(--ember-bright))]" />
            Researching what’s trending in your niche to plan fresh searches…
          </div>
        ) : failed ? (
          <p className="mt-3 text-sm text-destructive">
            Couldn’t build a search plan. {r?.error ?? ""} — close and try again.
          </p>
        ) : draft ? (
          <>
            <p className="mt-2 text-xs text-muted-foreground">
              These are the trending searches the scout will run. Edit, reword, or
              re-source them, then run — each run explores fresh ground.
            </p>
            {draft.themes.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {draft.themes.map((t, i) => (
                  <span
                    key={i}
                    className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
            <div className="mt-3 space-y-2">
              {draft.queries.map((q, i) => (
                <div key={i} className="flex items-start gap-2">
                  <select
                    value={q.source}
                    onChange={(e) =>
                      patchQuery(i, { source: e.target.value as PlanSource })
                    }
                    className="h-8 shrink-0 rounded-md border border-border bg-background px-1.5 text-xs"
                  >
                    {PLAN_SOURCES.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                  <div className="min-w-0 flex-1">
                    <Input
                      value={q.query}
                      onChange={(e) => patchQuery(i, { query: e.target.value })}
                      placeholder="search query…"
                      className="h-8 text-xs"
                    />
                    {q.rationale && (
                      <p className="mt-0.5 line-clamp-1 text-[11px] text-muted-foreground">
                        {q.rationale}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => removeQuery(i)}
                    className="mt-1 shrink-0 text-muted-foreground hover:text-destructive"
                    title="Remove query"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
            <button
              onClick={addQuery}
              className="mt-2 inline-flex items-center gap-1 text-xs text-[rgb(var(--ember))] hover:underline"
            >
              <Plus className="size-3.5" /> Add a query
            </button>

            <div className="mt-4 flex items-center gap-2">
              <Button variant="gold" size="sm" onClick={runIt} disabled={busy}>
                {busy ? <Loader2 className="animate-spin" /> : <Radar />}
                Run with this plan
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onClose}
                disabled={busy}
              >
                Discard
              </Button>
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

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

function RelinkCard({
  brandId,
  config,
  canEdit,
}: {
  brandId: string;
  config: RelinkConfig;
  canEdit: boolean;
}) {
  const update = useUpdateRelink(brandId);
  const run = useRunRelink(brandId);
  const field =
    "h-9 rounded-md border border-input bg-card px-2 text-sm outline-none focus:ring-1 focus:ring-[rgb(var(--ember))] disabled:opacity-60";

  const value = config.enabled ? config.cadence : "off";
  function change(v: string) {
    const body =
      v === "off"
        ? { enabled: false }
        : { enabled: true, cadence: v as RelinkConfig["cadence"] };
    update.mutate(body, {
      onSuccess: () => toast.success("Re-linking schedule saved"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Save failed"),
    });
  }

  return (
    <Card>
      <CardContent className="space-y-3 py-5">
        <div className="flex items-center gap-2">
          <Link2 className="size-4 text-[rgb(var(--ember))]" />
          <h3 className="text-sm font-semibold">Internal re-linking</h3>
        </div>
        <p className="text-xs text-muted-foreground">
          Periodically re-scans your <strong>published</strong> library and stages
          internal links between new and older articles. Suggestions appear on each
          article&apos;s <span className="font-medium">Links</span> tab for review —
          nothing is published automatically.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              Schedule
            </label>
            <select
              value={value}
              disabled={!canEdit || update.isPending}
              onChange={(e) => change(e.target.value)}
              className={cn(field, "w-44")}
            >
              <option value="off">Off — manual only</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          {canEdit && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                run.mutate(undefined, {
                  onSuccess: () =>
                    toast.success("Re-linking the library — check back shortly"),
                  onError: (e) =>
                    toast.error(e instanceof Error ? e.message : "Could not start"),
                })
              }
              disabled={run.isPending}
            >
              {run.isPending ? <Loader2 className="animate-spin" /> : <Link2 />}
              Run now
            </Button>
          )}
        </div>
        {config.last_run_at && (
          <p className="text-xs text-muted-foreground">
            Last run staged {config.last_found} link
            {config.last_found === 1 ? "" : "s"} for review.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function OpportunityCard({
  brandId,
  opp,
  clusterLabel,
}: {
  brandId: string;
  opp: Opportunity;
  clusterLabel?: string;
}) {
  const draft = useDraftOpportunity(brandId);
  const dismiss = useDismissOpportunity(brandId);
  const restore = useRestoreOpportunity(brandId);
  const del = useDeleteOpportunity(brandId);
  const busy = opp.status === "queued" || opp.status === "drafting";

  function onDelete() {
    if (!window.confirm(`Permanently delete “${opp.title}”?`)) return;
    del.mutate(opp.id, {
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed"),
    });
  }

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
            {opp.cluster_role === "pillar" ? (
              <Link
                href={`/brands/${brandId}/clusters`}
                className="inline-flex items-center gap-1 rounded bg-[rgb(var(--gold))]/12 px-1.5 py-0.5 text-[rgb(var(--gold))] hover:underline"
                title="Starts a new topic cluster as its pillar"
              >
                <Crown className="size-3" /> New cluster
              </Link>
            ) : opp.cluster_id ? (
              <Link
                href={`/brands/${brandId}/clusters`}
                className="inline-flex items-center gap-1 rounded bg-secondary px-1.5 py-0.5 hover:underline"
                title="Extends an existing topic cluster"
              >
                <Boxes className="size-3" />
                {clusterLabel ? `In ${clusterLabel}` : "In a cluster"}
              </Link>
            ) : null}
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
              <div className="flex flex-col gap-1">
                <span className="inline-flex items-center gap-1.5 text-xs text-[rgb(var(--ember-bright))]">
                  <Loader2 className="size-3.5 animate-spin" />
                  {opp.progress?.message ?? "Drafting…"}
                </span>
                {opp.article_id && (
                  <Link
                    href={`/brands/${brandId}/articles/${opp.article_id}`}
                    className="inline-flex items-center gap-1 text-xs font-medium text-[rgb(var(--ember))] hover:underline"
                  >
                    View live progress →
                  </Link>
                )}
              </div>
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
              <>
                <button
                  onClick={() => restore.mutate(opp.id)}
                  disabled={restore.isPending}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                >
                  <RotateCcw className="size-3" /> Restore
                </button>
                <button
                  onClick={onDelete}
                  disabled={del.isPending}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-destructive"
                >
                  <Trash2 className="size-3" /> Delete
                </button>
              </>
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
  const relinkConfig = useRelinkConfig(id);
  const runs = useScoutRuns(id);
  const opps = useOpportunities(id);
  const runScout = useRunScout(id);
  const planScout = usePlanScout(id);
  const [planRunId, setPlanRunId] = useState<string | null>(null);
  const clusters = useClusters(id);
  const clusterLabel = (cid?: string | null) =>
    cid ? clusters.data?.find((c) => c.id === cid)?.label : undefined;

  const lastRun = runs.data?.[0];
  const active = opps.data?.filter((o) => o.status !== "dismissed") ?? [];
  const dismissed = opps.data?.filter((o) => o.status === "dismissed") ?? [];

  const busyRun =
    runScout.isPending || lastRun?.status === "running" || !!planRunId;

  function run() {
    runScout.mutate(undefined, {
      onSuccess: () => toast.success("Scout started — discovering opportunities…"),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start scout"),
    });
  }

  function startPlan() {
    planScout.mutate(undefined, {
      onSuccess: (r) => setPlanRunId(r.id),
      onError: (e) =>
        toast.error(e instanceof Error ? e.message : "Could not start planning"),
    });
  }

  return (
    <Page>
      <PageHeader
        icon={Radar}
        title="Scouts"
        actions={
          canEdit && (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={run}
                disabled={busyRun}
                title="Auto-plan and run in one step"
              >
                {runScout.isPending ? <Loader2 className="animate-spin" /> : <Radar />}
                Quick run
              </Button>
              <Button
                variant="gold"
                size="sm"
                onClick={startPlan}
                disabled={busyRun || planScout.isPending}
              >
                {planScout.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Wand2 />
                )}
                Plan a run
              </Button>
            </div>
          )
        }
      />
      <PageBody>
      <p className="mb-5 text-sm text-muted-foreground">
        Scouts watch the market for timely, on-brand topics and surface scored
        opportunities here. <span className="font-medium">Plan a run</span> to research
        what’s trending and review the searches first; <span className="font-medium">
        Quick run</span> auto-plans and runs in one step. Run them as often as you like —
        each run explores fresh ground across news, YouTube, social, and the web.
      </p>

      {planRunId && (
        <SearchPlanPanel
          brandId={id}
          runId={planRunId}
          onClose={() => setPlanRunId(null)}
        />
      )}

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
        <div className="mb-4">
          <ConfigPanel brandId={id} config={config.data} canEdit={canEdit} />
        </div>
      )}

      {relinkConfig.data && (
        <div className="mb-6">
          <RelinkCard
            brandId={id}
            config={relinkConfig.data}
            canEdit={canEdit}
          />
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
          <OpportunityCard
            key={o.id}
            brandId={id}
            opp={o}
            clusterLabel={clusterLabel(o.cluster_id)}
          />
        ))}
      </div>

      {dismissed.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Dismissed ({dismissed.length})
          </summary>
          <div className="mt-3 grid gap-3">
            {dismissed.map((o) => (
              <OpportunityCard
            key={o.id}
            brandId={id}
            opp={o}
            clusterLabel={clusterLabel(o.cluster_id)}
          />
            ))}
          </div>
        </details>
      )}
      </PageBody>
    </Page>
  );
}
