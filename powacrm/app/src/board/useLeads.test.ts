import { describe, it, expect } from 'vitest';
import { groupByStage, leadName, selectResearchBatch, type Lead } from './useLeads';

const mk = (over: object) => ({ id: 'x', first_name: null, last_name: null, title: null,
  email: null, stage: 'sourced', position: 0, fit_score: null, company_id: null, company: null, ...over });

describe('groupByStage', () => {
  it('buckets leads by stage sorted by position', () => {
    const leads = [mk({ id: 'a', stage: 'sourced', position: 2 }), mk({ id: 'b', stage: 'sourced', position: 1 }), mk({ id: 'c', stage: 'won' })];
    const g = groupByStage(leads as any, ['sourced', 'won']);
    expect(g['sourced'].map((l: any) => l.id)).toEqual(['b', 'a']);
    expect(g['won'].map((l: any) => l.id)).toEqual(['c']);
  });
  it('always returns every stage key', () => {
    expect(groupByStage([], ['sourced', 'won'])).toEqual({ sourced: [], won: [] });
  });
});

describe('leadName', () => {
  it('joins first and last', () => expect(leadName(mk({ first_name: 'Ana', last_name: 'Lee' }) as any)).toBe('Ana Lee'));
  it('falls back to email', () => expect(leadName(mk({ email: 'x@y.z' }) as any)).toBe('x@y.z'));
  it('falls back to Unknown', () => expect(leadName(mk({}) as any)).toBe('Unknown'));
});

const withCompany = (id: string, companyId: string, researchedAt: string | null) =>
  mk({ id, company_id: companyId, company: { id: companyId, name: null, domain: null, researched_at: researchedAt } }) as Lead;

describe('selectResearchBatch', () => {
  it('excludes leads with no company', () => {
    const leads = [mk({ id: 'a' }) as Lead];
    expect(selectResearchBatch(leads)).toEqual([]);
  });

  it('excludes leads whose company was already researched', () => {
    const leads = [withCompany('a', 'co-1', '2026-01-01T00:00:00Z')];
    expect(selectResearchBatch(leads)).toEqual([]);
  });

  it('includes leads with a company and no researched_at', () => {
    const leads = [withCompany('a', 'co-1', null)];
    expect(selectResearchBatch(leads).map(l => l.id)).toEqual(['a']);
  });

  // The trap this exists to catch: ten leads at one company must spend one
  // slot in the batch, not ten -- a naive "first N leads" selection would
  // report back as if it queued ten jobs when request_research() actually
  // queues (and bills) exactly one.
  it('counts each unresearched company once, no matter how many leads share it', () => {
    const leads = [
      withCompany('a', 'co-1', null), withCompany('b', 'co-1', null), withCompany('c', 'co-1', null),
      withCompany('d', 'co-2', null),
    ];
    const batch = selectResearchBatch(leads, 10);
    expect(batch.map(l => l.id)).toEqual(['a', 'd']);
  });

  it('caps at the requested size, counted in distinct companies', () => {
    const leads = [withCompany('a', 'co-1', null), withCompany('b', 'co-2', null), withCompany('c', 'co-3', null)];
    expect(selectResearchBatch(leads, 2).map(l => l.id)).toEqual(['a', 'b']);
  });

  it('defaults to a batch size of 10', () => {
    const leads = Array.from({ length: 15 }, (_, i) => withCompany(`l${i}`, `co-${i}`, null));
    expect(selectResearchBatch(leads)).toHaveLength(10);
  });
});
