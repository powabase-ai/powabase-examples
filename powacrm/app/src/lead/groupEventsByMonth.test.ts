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

it('files an event under the month the reader actually sees in its tooltip', () => {
  // Timeline.tsx renders the tooltip with toLocaleString(). Grouping on UTC put
  // an event just after midnight UTC under the following month while its own
  // tooltip still read the previous one. Deriving both from the same local clock
  // keeps the header and the row consistent wherever the reader is.
  const iso = '2026-08-01T02:00:00Z';
  const [g] = groupEventsByMonth([ev('a', iso)]);
  const d = new Date(iso);
  const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  expect(g.label).toBe(`${MONTHS[d.getMonth()]} ${d.getFullYear()}`);
});
