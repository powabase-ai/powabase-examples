import { useMutation, useQuery } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';

export type ResearchVerdict = 'queued' | 'already_queued' | 'skipped' | 'capped' | 'not_yours';
export type ResearchResult = { person_id: string; verdict: ResearchVerdict; job_id: string | null; detail: string | null };

export type ResearchJob = {
  id: string; company_id: string; status: 'queued' | 'running' | 'done' | 'failed' | 'skipped';
  attempts: number; error: string | null; started_at: string | null; finished_at: string | null; created_at: string;
};

// One human sentence out of a batch of verdicts. Counting is not enough on its
// own -- "3 skipped" tells the caller nothing they can act on, and a cap needs
// to read as different from a routine skip because only the cap is something
// the user can do anything about (raise it, or wait for tomorrow).
export function summarizeVerdicts(results: ResearchResult[]): string {
  if (results.length === 0) return 'Nothing to research';

  const queued = results.filter(r => r.verdict === 'queued').length;
  const alreadyQueued = results.filter(r => r.verdict === 'already_queued').length;
  const skipped = results.filter(r => r.verdict === 'skipped');
  const capped = results.filter(r => r.verdict === 'capped');
  const notYours = results.filter(r => r.verdict === 'not_yours').length;

  const parts: string[] = [];
  if (queued > 0) parts.push(`${queued} queued`);
  if (alreadyQueued > 0) parts.push(`${alreadyQueued} already queued`);

  // Skips are grouped by their detail reason so "no domain" and "researched
  // recently" don't collapse into one meaningless "N skipped".
  for (const [detail, count] of groupByDetail(skipped, 'skipped')) {
    parts.push(`${count} skipped (${shortenDetail(detail)})`);
  }

  // Capped entries keep their RPC detail VERBATIM -- NOT run through
  // shortenDetail. "daily cap of 25 reached (25 used today)" carries the one
  // actionable number in this whole summary (exactly what to go raise on the
  // settings page), so trimming it the way skip reasons are trimmed would
  // throw away the only useful part. Grouped the same way skips are: every
  // capped result in one batch shares the same cap/used snapshot, because
  // request_research() stops inserting the moment the cap is hit, so v_used
  // never changes again for the rest of that call -- this never repeats the
  // same message N times.
  for (const [detail, count] of groupByDetail(capped, 'capped')) {
    parts.push(`${count} capped (${detail})`);
  }

  // `not_yours`'s detail is deliberately uninformative -- "no such lead, or
  // not yours" collapses "doesn't exist" and "exists but isn't yours" into
  // one answer on purpose, so the verdict itself can't become an existence
  // oracle for rows the caller can't read (see request_research()'s comment
  // in db/migrations/0012_research_rpcs.sql). Surfacing it here would add
  // words, not information, so -- unlike skipped/capped -- this stays a flat
  // count.
  if (notYours > 0) parts.push(`${notYours} not yours`);

  return parts.join(' · ');
}

function groupByDetail(results: ResearchResult[], fallback: string): [string, number][] {
  const byDetail = new Map<string, number>();
  for (const r of results) {
    const key = r.detail ?? fallback;
    byDetail.set(key, (byDetail.get(key) ?? 0) + 1);
  }
  return [...byDetail];
}

// The RPC's skip `detail` strings are full sentences, written for a log, not
// a one-line summary sitting next to a count. This trims the common ones
// down to what the test cases (and a person skimming the banner) actually
// need. Capped details are NOT passed through this -- see the comment above.
function shortenDetail(detail: string): string {
  if (detail.includes('no domain')) return 'no domain';
  if (detail.includes('within the last 30 days')) return 'researched recently';
  if (detail.includes('no company')) return 'no company';
  return detail;
}

export function useRequestResearch(brandId: string) {
  return useMutation({
    mutationFn: async (personIds: string[]) => {
      const { data, error } = await supabase.rpc('request_research', { _person_ids: personIds });
      if (error) throw error;
      return ((data as { results: ResearchResult[] }).results) ?? [];
    },
    // brandId is not sent to the RPC (it resolves ownership itself off the
    // person ids), but every other mutation in this app is scoped by brand id
    // for cache invalidation, and Task 8 needs a stable per-brand hook shape.
    meta: { brandId },
  });
}

// Polls the newest research_jobs row for a company. `refetchInterval` is
// conditional on the fetched status, not just "while a job exists": once a job
// lands in `done`/`failed`/`skipped` the interval must stop, or this keeps
// hitting the database forever for a demo tab someone left open.
export function useResearchJob(companyId: string | null) {
  return useQuery({
    queryKey: ['research_job', companyId],
    enabled: companyId != null,
    queryFn: async () => {
      const { data, error } = await supabase.from('research_jobs')
        .select('id,company_id,status,attempts,error,started_at,finished_at,created_at')
        .eq('company_id', companyId!).order('created_at', { ascending: false }).limit(1).maybeSingle();
      if (error) throw error;
      return data as ResearchJob | null;
    },
    refetchInterval: query => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' ? 5000 : false;
    },
  });
}
