import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/powabase';
import { currentActorName } from '@/lib/actor';
import { useBrand } from '@/shell/BrandContext';
import { StageTag } from '@/board/StageTag';
import { leadName, useStages, type Lead } from '@/board/useLeads';
import { InlineField } from './InlineField';
import { Timeline } from './Timeline';
import type { TimelineEvent } from './groupEventsByMonth';
import { summarizeVerdicts, useRequestResearch, useResearchJob } from '@/research/useResearch';
import { ResearchPanel, type ResearchData } from '@/research/ResearchPanel';

type FullLead = Lead & {
  linkedin_url: string | null; created_at: string; company_id: string | null;
  company: {
    id: string; name: string | null; domain: string | null; research: string | null;
    research_data: ResearchData | null; tech_stack: unknown; researched_at: string | null;
  } | null;
};

export function LeadPage() {
  const { id } = useParams<{ id: string }>();
  const { brand } = useBrand();
  const stagesQuery = useStages();
  const { data: stages } = stagesQuery;
  const qc = useQueryClient();
  const [tab, setTab] = useState<'activity' | 'research'>('activity');
  const [note, setNote] = useState('');

  const leadQuery = useQuery({
    queryKey: ['lead', id],
    // PGRST116 (no rows from `.single()`) is a permanent answer for this id --
    // retrying it just delays the "not found" message.
    retry: false,
    queryFn: async () => {
      const { data, error } = await supabase.from('people')
        .select('id,first_name,last_name,title,email,stage,position,fit_score,linkedin_url,created_at,company_id,company:companies(id,name,domain,research,research_data,tech_stack,researched_at)')
        .eq('id', id!).single();
      if (error) throw error;
      return data as unknown as FullLead;
    },
  });
  const { data: lead } = leadQuery;
  const eventsQuery = useQuery({
    queryKey: ['events', id],
    queryFn: async () => {
      const { data, error } = await supabase.from('events')
        .select('id,event_type,happens_at,actor_name,actor_source,properties')
        .eq('person_id', id!).order('happens_at', { ascending: false }).limit(200);
      if (error) throw error;
      return data as TimelineEvent[];
    },
  });
  const { data: events } = eventsQuery;

  const patch = useMutation({
    mutationFn: async (fields: Partial<FullLead>) => {
      const { error } = await supabase.from('people').update(fields).eq('id', id!);
      if (error) throw error;
    },
    onSettled: () => { qc.invalidateQueries({ queryKey: ['lead', id] }); qc.invalidateQueries({ queryKey: ['leads', brand.id] }); },
  });
  const addNote = useMutation({
    mutationFn: async (body: string) => {
      const { error } = await supabase.from('events').insert({
        brand_id: brand.id, person_id: id, company_id: lead?.company_id ?? null,
        event_type: 'note', actor_source: 'MANUAL', actor_name: await currentActorName(), properties: { body },
      });
      if (error) throw error;
    },
    // Clear the box only once the insert has landed. Clearing it at fire time
    // destroyed whatever the user typed if the insert then failed, with nothing
    // shown to explain where it went.
    onSuccess: () => setNote(''),
    onSettled: () => qc.invalidateQueries({ queryKey: ['events', id] }),
  });

  const companyId = lead?.company_id ?? null;
  const jobQuery = useResearchJob(companyId);
  const { data: job } = jobQuery;
  const requestResearch = useRequestResearch(brand.id);
  const [researchSummary, setResearchSummary] = useState<string | null>(null);
  const runResearch = () => {
    if (!id) return;
    setResearchSummary(null);
    requestResearch.mutate([id], {
      onSuccess: results => {
        setResearchSummary(summarizeVerdicts(results));
        qc.invalidateQueries({ queryKey: ['research_job', companyId] });
      },
    });
  };

  // `/leads/:id` is bookmarkable and hand-typeable, and `.single()` answers a
  // missing (or soft-deleted, since the SELECT policy hides tombstones) id with
  // HTTP 406 / PGRST116. Without this branch a stale bookmark showed "Loading…"
  // forever, with no message and no way back.
  const loadError = leadQuery.error ?? stagesQuery.error ?? eventsQuery.error;
  // Same reasoning as BrandContext: a failed refetch keeps the cached data, and
  // replacing a perfectly good record page with an error screen (losing whatever
  // the user was mid-edit) is worse than showing slightly stale data.
  if (loadError && !lead) {
    const notFound = (loadError as { code?: string }).code === 'PGRST116';
    return (
      <div>
        <h1 style={{ fontSize: 'var(--font-lg)', marginTop: 0 }}>{notFound ? 'Lead not found' : "Couldn't load this lead"}</h1>
        <p style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-sm)' }}>
          {notFound ? 'It may have been deleted, or the link may be wrong.' : loadError.message}
        </p>
        <Link to="/">← Back to the pipeline</Link>
      </div>
    );
  }
  if (!lead || !stages || !events) return <p>Loading…</p>;
  const stage = stages.find(s => s.value === lead.stage);
  const days = Math.round((Date.now() - new Date(lead.created_at).getTime()) / 86400000);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 'var(--space-6)', height: '100%' }}>
      <aside style={{ background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)' }}>
        <h2 style={{ margin: 0, fontSize: 'var(--font-lg)' }}>{leadName(lead)}</h2>
        <p title={new Date(lead.created_at).toLocaleString()}
          style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)' }}>Added {days}d ago</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          {stage && <StageTag label={stage.label} color={stage.color} />}
          {/* Hidden rather than disabled when there's no company -- there is
              nothing an RPC round trip could tell the user that "no company"
              doesn't already say. */}
          {lead.company_id && (
            <button onClick={runResearch}
              disabled={requestResearch.isPending || job?.status === 'queued' || job?.status === 'running'}>
              {requestResearch.isPending ? 'Requesting…' : 'Research'}
            </button>
          )}
        </div>
        {job?.status === 'queued' && (
          <p style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)', marginTop: 'var(--space-1)' }}>Queued…</p>
        )}
        {job?.status === 'running' && (
          <p style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)', marginTop: 'var(--space-1)' }}>Researching…</p>
        )}
        {job?.status === 'failed' && (
          <div style={{ fontSize: 'var(--font-xs)', color: 'var(--tag-red-fg)', marginTop: 'var(--space-1)' }}>
            {/* The job's own error is the most useful thing on the screen when
                a run fails -- it names the site or the reason, not just "failed". */}
            Failed: {job.error ?? 'unknown error'}{' '}
            <button onClick={runResearch} disabled={requestResearch.isPending}>Retry</button>
          </div>
        )}
        {researchSummary && !requestResearch.isPending && (
          <p style={{ fontSize: 'var(--font-xs)', color: 'var(--fg-tertiary)', marginTop: 'var(--space-1)' }}>{researchSummary}</p>
        )}
        <div style={{ marginTop: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
          <InlineField label="First name" value={lead.first_name} onSave={v => patch.mutate({ first_name: v })} />
          <InlineField label="Last name" value={lead.last_name} onSave={v => patch.mutate({ last_name: v })} />
          <InlineField label="Title" value={lead.title} onSave={v => patch.mutate({ title: v })} />
          <InlineField label="Email" value={lead.email} onSave={v => patch.mutate({ email: v })} />
          <InlineField label="LinkedIn" value={lead.linkedin_url} onSave={v => patch.mutate({ linkedin_url: v })} />
        </div>
        {lead.company && (
          <p style={{ fontSize: 'var(--font-sm)', color: 'var(--fg-secondary)', marginTop: 'var(--space-4)' }}>
            {lead.company.name} {lead.company.domain && `· ${lead.company.domain}`}
          </p>
        )}
      </aside>
      <section>
        <div style={{ display: 'flex', gap: 'var(--space-3)', borderBottom: '1px solid var(--border-light)', marginBottom: 'var(--space-4)' }}>
          {(['activity', 'research'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{ background: 'none', border: 'none', cursor: 'pointer',
              padding: 'var(--space-2) 0', fontSize: 'var(--font-sm)',
              color: tab === t ? 'var(--fg-primary)' : 'var(--fg-tertiary)',
              borderBottom: tab === t ? '2px solid var(--fg-primary)' : '2px solid transparent' }}>
              {t === 'activity' ? 'Activity' : 'Research'}
            </button>
          ))}
        </div>
        {(patch.error || addNote.error || requestResearch.error || (jobQuery.error && !job)) && (
          <div style={{ background: 'var(--tag-red-bg)', color: 'var(--tag-red-fg)', padding: 'var(--space-3)',
            borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)', marginBottom: 'var(--space-3)' }}>
            {/* Without this a rejected write just snapped the field back, which reads
                as "I mistyped it" rather than "the server refused". Gate the job-poll
                error on `!job`, same reasoning as BrandContext: a failed background
                refetch in TanStack Query v5 retains the last good `data`, so a
                momentary blip on the 5s poll must not blank out a status that's
                still perfectly valid. */}
            Couldn't save: {(patch.error ?? addNote.error ?? requestResearch.error ?? jobQuery.error)!.message}
          </div>
        )}
        {tab === 'activity' ? (
          <>
            <form onSubmit={e => { e.preventDefault(); if (note.trim()) addNote.mutate(note.trim()); }}
              style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
              <input value={note} onChange={e => setNote(e.target.value)} placeholder="Add a note…" style={{ flex: 1 }}
                disabled={addNote.isPending} />
              <button type="submit" disabled={addNote.isPending}>{addNote.isPending ? 'Adding…' : 'Add'}</button>
            </form>
            <Timeline events={events} />
          </>
        ) : (
          <ResearchPanel company={lead.company} />
        )}
      </section>
    </div>
  );
}
