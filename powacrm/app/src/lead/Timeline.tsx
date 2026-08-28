import { groupEventsByMonth, type TimelineEvent } from './groupEventsByMonth';
import { asText } from '@/lib/asText';

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

// EVERY VALUE READ HERE IS UNTRUSTED, AND SO IS ITS TYPE. `events.properties`
// is jsonb, and for a `researched` row complete_research_job writes it straight
// from the agent's payload -- the agent having just read an attacker-controlled
// web page. `String(x)` is not a safe way to render that: it runs ToPrimitive
// and RAISES on an object with a non-function `toString`, which is the exact
// hazard the research panel had removed next door while this file still carried
// it (`String(p?.body ?? ...)` and a bare `${p.diff.stage.after}` template).
// Everything interpolated below goes through asText, which cannot throw.
//
// The `researched` branch is the other half. Without it the row fell through to
// `event_type.replace('_',' ')` and read "Researcher researched 3m ago" -- the
// feature the release is about, rendered as a shrug, with the score, the
// rationale and the injection flag the event actually carries all silently
// dropped. The design spec promised "Researcher scored Acme 82".
function sentence(e: TimelineEvent): string {
  const p = e.properties as Record<string, unknown> | null | undefined;
  if (e.event_type === 'stage_changed') {
    const stage = (p?.diff as { stage?: { after?: unknown } } | undefined)?.stage;
    if (stage) return `moved to ${asText(stage.after)}`;
  }
  if (e.event_type === 'note') return p?.body === undefined || p?.body === null
    ? 'added a note' : asText(p.body);
  if (e.event_type === 'import') return 'imported from CSV';
  if (e.event_type === 'researched') return researchedSentence(p);
  return e.event_type.replace('_', ' ');
}

// "scored Acme 82", with the rationale after it when there is one, and the
// injection warning first when the agent flagged the page -- that flag is the
// one thing in the row a seller must not miss, so it leads.
//
// The score is deliberately not coerced with Number(): 0012 stores it as an
// int, but a row written before that validation, or a payload shape that
// changes later, could hold anything, and asText renders whatever is there
// rather than turning it into NaN.
function researchedSentence(p: Record<string, unknown> | null | undefined): string {
  const score = p?.score === undefined || p?.score === null ? '' : asText(p.score);
  const rationale = asText(p?.rationale).trim();
  // Same rule complete_research_job and ResearchPanel apply: anything that is
  // not an explicit false/null/absent/blank counts as observed, because the
  // failure that matters is not showing the warning.
  const flagged = p?.injection_observed !== undefined && p?.injection_observed !== null
    && p?.injection_observed !== false
    && !(typeof p.injection_observed === 'string'
         && (p.injection_observed.trim() === '' || p.injection_observed.trim().toLowerCase() === 'false'));

  const head = score === '' ? 'researched this lead' : `scored this lead ${score}`;
  const parts = [flagged ? `⚠ flagged the page for injected instructions — ${head}` : head];
  if (rationale) parts.push(`— ${rationale}`);
  return parts.join(' ');
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
