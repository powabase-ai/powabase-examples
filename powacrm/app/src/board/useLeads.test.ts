import { describe, it, expect } from 'vitest';
import { groupByStage, leadName } from './useLeads';

const mk = (over: object) => ({ id: 'x', first_name: null, last_name: null, title: null,
  email: null, stage: 'sourced', position: 0, fit_score: null, company: null, ...over });

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
