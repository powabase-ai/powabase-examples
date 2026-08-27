import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { currentActorName } from '@/lib/actor';

export type Lead = {
  id: string; first_name: string | null; last_name: string | null; title: string | null;
  email: string | null; stage: string; position: number; fit_score: number | null;
  company: { id: string; name: string | null; domain: string | null } | null;
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

export function useLeads(brandId: string) {
  return useQuery({
    queryKey: ['leads', brandId],
    queryFn: async () => {
      const { data, error } = await supabase.from('people')
        .select('id,first_name,last_name,title,email,stage,position,fit_score,company:companies(id,name,domain)')
        .eq('brand_id', brandId).is('deleted_at', null).limit(1000);
      if (error) throw error;
      return data as unknown as Lead[];
    },
  });
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
