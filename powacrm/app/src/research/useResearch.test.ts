import { describe, it, expect } from 'vitest';
import {
  isResearchInFlight, researchJobJustFinished, researchNote, summarizeVerdicts,
  type ResearchJob, type ResearchResult,
} from './useResearch';

const r = (verdict: ResearchResult['verdict'], detail: string | null = null): ResearchResult =>
  ({ person_id: 'p', verdict, job_id: null, detail });

describe('summarizeVerdicts', () => {
  it('reports a plain success', () => {
    expect(summarizeVerdicts([r('queued'), r('queued')])).toBe('2 queued');
  });
  it('names why leads were skipped rather than just counting them', () => {
    const s = summarizeVerdicts([r('queued'), r('skipped', 'company has no domain to research')]);
    expect(s).toContain('1 queued');
    expect(s).toContain('no domain');
  });
  it('calls out the cap with its real numbers, because that one needs action', () => {
    // Asserts on the actual numbers from a realistic RPC detail, not just the
    // word "cap" -- a flat "N over the daily cap" label would satisfy a bare
    // `.toContain('daily cap')` without ever telling the user it's 25 of 25.
    const s = summarizeVerdicts([r('capped', 'daily cap of 25 reached (25 used today)')]);
    expect(s).toContain('daily cap');
    expect(s).toContain('25');
  });
  it('collapses already-queued into something honest', () => {
    expect(summarizeVerdicts([r('already_queued')])).toContain('already');
  });
  it('handles an empty result set', () => {
    expect(summarizeVerdicts([])).toBe('Nothing to research');
  });
});

const snap = (id: string, status: ResearchJob['status']) => ({ id, status });
const job = (status: ResearchJob['status'], error: string | null = null) => ({ status, error });

describe('isResearchInFlight', () => {
  it('treats queued and running as in flight and everything else as settled', () => {
    expect(isResearchInFlight('queued')).toBe(true);
    expect(isResearchInFlight('running')).toBe(true);
    expect(isResearchInFlight('done')).toBe(false);
    expect(isResearchInFlight('failed')).toBe(false);
    expect(isResearchInFlight('skipped')).toBe(false);
    expect(isResearchInFlight(null)).toBe(false);
    expect(isResearchInFlight(undefined)).toBe(false);
  });
});

describe('researchJobJustFinished', () => {
  it('fires on the transition a one-tick-one-transaction worker actually produces', () => {
    // The worker never publishes `running`, so queued -> done is the real
    // shape and the one that has to refresh the page.
    expect(researchJobJustFinished(snap('j1', 'queued'), snap('j1', 'done'))).toBe(true);
  });
  it('fires on a failure too, because the error is also a result to show', () => {
    expect(researchJobJustFinished(snap('j1', 'queued'), snap('j1', 'failed'))).toBe(true);
    expect(researchJobJustFinished(snap('j1', 'running'), snap('j1', 'skipped'))).toBe(true);
  });
  it('does not fire while the job is still in flight', () => {
    expect(researchJobJustFinished(snap('j1', 'queued'), snap('j1', 'queued'))).toBe(false);
    expect(researchJobJustFinished(snap('j1', 'queued'), snap('j1', 'running'))).toBe(false);
  });
  it('does not fire on first sight of an already-finished job', () => {
    // Opening a lead researched last week must not kick off an invalidation
    // storm on every mount.
    expect(researchJobJustFinished(null, snap('j1', 'done'))).toBe(false);
    expect(researchJobJustFinished(snap('j1', 'done'), snap('j1', 'done'))).toBe(false);
  });
  it('does not mistake a NEW job for the old one finishing', () => {
    expect(researchJobJustFinished(snap('j1', 'queued'), snap('j2', 'done'))).toBe(false);
  });
  it('does not fire when the row goes away', () => {
    expect(researchJobJustFinished(snap('j1', 'queued'), null)).toBe(false);
  });
});

describe('researchNote', () => {
  it('says something for every terminal status -- a finished run must never render nothing', () => {
    expect(researchNote(job('done'))).not.toBeNull();
    expect(researchNote(job('failed'))).not.toBeNull();
    expect(researchNote(job('skipped'))).not.toBeNull();
  });
  it('shows the run as complete rather than silently blanking', () => {
    const n = researchNote(job('done'))!;
    expect(n.tone).toBe('ok');
    expect(n.text).toMatch(/complete/i);
    expect(n.retry).toBe(false);
  });
  it('surfaces the job error verbatim and offers a retry', () => {
    const n = researchNote(job('failed', 'agent stream HTTP 502: upstream'))!;
    expect(n.tone).toBe('error');
    expect(n.text).toContain('agent stream HTTP 502');
    expect(n.retry).toBe(true);
  });
  it('still says something when a failure carries no error text', () => {
    expect(researchNote(job('failed'))!.text).toContain('unknown error');
  });
  it('gives queued and running the same in-flight line, since running is never observed', () => {
    expect(researchNote(job('queued'))).toEqual(researchNote(job('running')));
    expect(researchNote(job('queued'))!.tone).toBe('muted');
  });
  it('renders nothing when there is no job at all', () => {
    expect(researchNote(null)).toBeNull();
    expect(researchNote(undefined)).toBeNull();
  });
});
