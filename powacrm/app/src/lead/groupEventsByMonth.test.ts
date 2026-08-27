import { describe, it, expect } from 'vitest';
import { groupEventsByMonth } from './groupEventsByMonth';

const ev = (id: string, iso: string) => ({ id, event_type: 'note', happens_at: iso, actor_name: 'x', actor_source: 'MANUAL', properties: {} });

describe('groupEventsByMonth', () => {
  it('groups by month, newest group and newest item first', () => {
    const g = groupEventsByMonth([ev('a', '2026-07-01T10:00:00Z'), ev('b', '2026-08-20T10:00:00Z'), ev('c', '2026-08-02T10:00:00Z')]);
    expect(g.map(x => x.label)).toEqual(['August 2026', 'July 2026']);
    expect(g[0].items.map(i => i.id)).toEqual(['b', 'c']);
  });
  it('returns empty array for no events', () => expect(groupEventsByMonth([])).toEqual([]));
});
