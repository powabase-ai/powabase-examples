import { describe, it, expect } from 'vitest';
import { summarizeVerdicts, type ResearchResult } from './useResearch';

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
