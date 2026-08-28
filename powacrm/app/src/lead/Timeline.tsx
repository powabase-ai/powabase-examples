import { groupEventsByMonth, type TimelineEvent } from './groupEventsByMonth';

// `researched` is this phase's own event type and it was missing here, so the
// feature the release is about rendered with the generic bullet. Nothing in
// the type system catches that: ICONS is a Record<string, string>, so every
// lookup type-checks and a missing key is only visible on screen.
const ICONS: Record<string, string> = { note: '📝', stage_changed: '→', field_updated: '✏️', import: '⬆', researched: '🔍', error: '⚠' };

function rel(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function sentence(e: TimelineEvent): string {
  const p = e.properties as any;
  if (e.event_type === 'stage_changed' && p?.diff?.stage) return `moved to ${p.diff.stage.after}`;
  if (e.event_type === 'note') return String(p?.body ?? 'added a note');
  if (e.event_type === 'import') return 'imported from CSV';
  return e.event_type.replace('_', ' ');
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const groups = groupEventsByMonth(events);
  if (!groups.length) return <p style={{ color: 'var(--fg-light)' }}>No activity yet.</p>;
  return (
    <div style={{ display: 'grid', gap: 'var(--space-4)' }}>
      {groups.map(g => (
        <div key={g.label}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
            <span style={{ fontSize: 'var(--font-xs)', fontWeight: 600, color: 'var(--fg-light)' }}>{g.label}</span>
            <div style={{ flex: 1, height: 1, background: 'var(--border-light)' }} />
          </div>
          {g.items.map((e, i) => (
            <div key={e.id} style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <div style={{ display: 'grid', justifyItems: 'center' }}>
                <span style={{ fontSize: 14 }}>{ICONS[e.event_type] ?? '•'}</span>
                {i < g.items.length - 1 && <div style={{ width: 2, flex: 1, background: 'var(--border-light)' }} />}
              </div>
              <div style={{ paddingBottom: 'var(--space-3)', fontSize: 'var(--font-sm)' }}>
                <span style={{ fontWeight: 500 }}>{e.actor_name}</span> {sentence(e)}{' '}
                <span title={new Date(e.happens_at).toLocaleString()} style={{ color: 'var(--fg-tertiary)' }}>{rel(e.happens_at)}</span>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
