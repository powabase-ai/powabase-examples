import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { currentActorName } from '@/lib/actor';

export type Lead = {
  id: string; first_name: string | null; last_name: string | null; title: string | null;
  email: string | null; stage: string; position: number; fit_score: number | null;
  company_id: string | null;
  company: { id: string; name: string | null; domain: string | null; researched_at: string | null } | null;
};
export type Stage = { value: string; label: string; color: string; position: number };

export function leadName(l: Lead): string {
  const n = [l.first_name, l.last_name].filter(Boolean).join(' ');
  return n || l.email || 'Unknown';
}

export function groupByStage(leads: Lead[], stageValues: string[]): Record<string, Lead[]> {
  const g: Record<string, Lead[]> = Object.fromEntries(stageValues.map(s => [s, []]));
  for (const l of leads) (g[l.stage] ?? (g[l.stage] = [])).push(l);
  for (const s of Object.keys(g)) g[s].sort((a, b) => a.position - b.position);
  return g;
}

export function useStages() {
  return useQuery({
    queryKey: ['stages'],
    queryFn: async () => {
      const { data, error } = await supabase.from('stage_options')
        .select('value,label,color,position').eq('object', 'people').order('position');
      if (error) throw error;
      return data as Stage[];
    },
  });
}

// The board renders every lead it fetches, so it has to stop somewhere. The cap
// is surfaced in the UI rather than silently truncating: with an ORDER BY the
// same 1000 come back every time, but a per-stage count computed from a
// truncated set is still not the brand's real count.
export const LEAD_CAP = 1000;

export function useLeads(brandId: string) {
  return useQuery({
    queryKey: ['leads', brandId],
    queryFn: async () => {
      const { data, error } = await supabase.from('people')
        .select('id,first_name,last_name,title,email,stage,position,fit_score,company_id,company:companies(id,name,domain,researched_at)')
        // Order before limiting. Without an ORDER BY, *which* 1000 rows Postgres
        // returns is arbitrary and changes after any write, so on a larger brand
        // a single drag made unrelated cards appear and vanish.
        .eq('brand_id', brandId).is('deleted_at', null)
        .order('position', { ascending: true }).order('id', { ascending: true })
        .limit(LEAD_CAP);
      if (error) throw error;
      return data as unknown as Lead[];
    },
  });
}

// Research is billed per COMPANY, not per lead: ten leads at one
// unresearched company cost one research job, not ten (request_research()'s
// partial unique index on research_jobs.company_id enforces this server-side
// -- a second person at an already-queued company comes back `already_queued`,
// not a second job). Picking "the first N leads" by board order without
// de-duplicating would let one company with many leads eat the whole batch,
// so a click asking for 10 could spend a single job and report back as if it
// tried ten. This de-dupes by company_id BEFORE capping at `size`, so both
// the returned array's length and the button label built from it mean "N
// distinct companies" -- the unit a click here actually spends.
export const RESEARCH_BATCH_SIZE = 10;

export function selectResearchBatch(leads: Lead[], size = RESEARCH_BATCH_SIZE): Lead[] {
  const seenCompanies = new Set<string>();
  const batch: Lead[] = [];
  for (const lead of leads) {
    if (!lead.company_id || lead.company?.researched_at) continue;
    if (seenCompanies.has(lead.company_id)) continue;
    seenCompanies.add(lead.company_id);
    batch.push(lead);
    if (batch.length >= size) break;
  }
  return batch;
}

export function useMoveLead(brandId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ lead, toStage, position }: { lead: Lead; toStage: string; position: number }) => {
      const { error } = await supabase.from('people').update({ stage: toStage, position }).eq('id', lead.id);
      if (error) throw error;
      // The stage write above is authoritative; this timeline event is a secondary,
      // advisory record. If it fails, the persisted stage change must NOT be rolled
      // back (onError would revert the optimistic cache to the old stage while the
      // DB holds the new one — the UI would then be lying about a move that actually
      // succeeded). So its error is logged, not thrown. A transactional write across
      // both tables would need a database RPC, which phase 1 does not warrant.
      const { error: eventError } = await supabase.from('events').insert({
        brand_id: brandId, person_id: lead.id, event_type: 'stage_changed',
        actor_source: 'MANUAL', actor_name: await currentActorName(),
        properties: { diff: { stage: { before: lead.stage, after: toStage } } },
      });
      if (eventError) {
        console.error(`useMoveLead: stage_changed event failed for lead ${lead.id} (${lead.stage} -> ${toStage})`, eventError);
      }
    },
    onMutate: async ({ lead, toStage, position }) => {
      await qc.cancelQueries({ queryKey: ['leads', brandId] });
      const prev = qc.getQueryData<Lead[]>(['leads', brandId]);
      qc.setQueryData<Lead[]>(['leads', brandId], old =>
        (old ?? []).map(l => (l.id === lead.id ? { ...l, stage: toStage, position } : l)));
      return { prev };
    },
    onError: (_e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(['leads', brandId], ctx.prev); },
    onSettled: () => qc.invalidateQueries({ queryKey: ['leads', brandId] }),
  });
}
