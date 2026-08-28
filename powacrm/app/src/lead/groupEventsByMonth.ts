export type TimelineEvent = {
  id: string; event_type: string; happens_at: string;
  actor_name: string; actor_source: string; properties: Record<string, unknown>;
};
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

// Month grouping, not day. [Twenty]
export function groupEventsByMonth(events: TimelineEvent[]) {
  const sorted = [...events].sort((a, b) => b.happens_at.localeCompare(a.happens_at));
  const groups: { year: number; month: number; label: string; items: TimelineEvent[] }[] = [];
  for (const e of sorted) {
    const d = new Date(e.happens_at);
    // Local, not UTC. Timeline.tsx renders each row's tooltip with
    // toLocaleString(), so grouping on UTC put an event at 2026-08-01T02:00Z
    // under "August 2026" with a tooltip reading 7/31/2026 in New York -- the
    // header contradicting the row beneath it. Whichever clock the reader is on,
    // the two now agree.
    const y = d.getFullYear(), m = d.getMonth();
    const last = groups[groups.length - 1];
    if (last && last.year === y && last.month === m) last.items.push(e);
    else groups.push({ year: y, month: m, label: `${MONTHS[m]} ${y}`, items: [e] });
  }
  return groups;
}
