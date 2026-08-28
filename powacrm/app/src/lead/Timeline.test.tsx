// Timeline renders `events.properties`, which is jsonb, and for a `researched`
// row complete_research_job writes it straight from the agent's payload -- the
// agent having just read an attacker-controlled web page. So this file covers
// the same two things ResearchPanel.test.tsx covers, for the same reason:
//
//   * the row says what actually happened, rather than falling through to
//     `event_type.replace('_',' ')` and reading "Researcher researched";
//   * nothing rendered here can throw, including a value that `String()` cannot
//     convert at all.
import { describe, it, expect, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Timeline } from './Timeline';
import type { TimelineEvent } from './groupEventsByMonth';

afterEach(cleanup);

const at = new Date(Date.now() - 3 * 60_000).toISOString();

function ev(over: Partial<TimelineEvent>): TimelineEvent {
  return {
    id: 'e1', event_type: 'note', happens_at: at,
    actor_name: 'Researcher', actor_source: 'AGENT', properties: {},
    ...over,
  };
}

describe('Timeline', () => {
  it('reports the score a research run produced, not just that one happened', () => {
    // Red before this fix: `sentence()` had no `researched` branch, so the row
    // fell through to event_type.replace('_',' ') and read "Researcher
    // researched" -- the release's headline feature rendered as a shrug, with
    // the score, the rationale and the injection flag on the event all dropped.
    render(<Timeline events={[ev({
      event_type: 'researched',
      properties: { score: 82, rationale: 'Runs a support team on Zendesk', injection_observed: false },
    })]} />);
    expect(screen.getByText(/scored this lead 82/)).toBeTruthy();
    expect(screen.getByText(/Runs a support team on Zendesk/)).toBeTruthy();
  });

  it('leads with the injection warning when the agent flagged the page', () => {
    render(<Timeline events={[ev({
      event_type: 'researched',
      properties: { score: 10, rationale: null, injection_observed: true },
    })]} />);
    expect(screen.getByText(/flagged the page for injected instructions/)).toBeTruthy();
  });

  it('treats a non-boolean injection_observed as observed, like the server does', () => {
    // A model reaching for prose on a boolean field ("detected on the pricing
    // page") is ordinary output, and the failure that matters is not showing
    // the warning. Same rule as complete_research_job and ResearchPanel.
    render(<Timeline events={[ev({
      event_type: 'researched',
      properties: { score: 40, injection_observed: 'maybe, on the pricing page' },
    })]} />);
    expect(screen.getByText(/flagged the page for injected instructions/)).toBeTruthy();
  });

  it('does not warn for an explicit "false" string', () => {
    render(<Timeline events={[ev({
      event_type: 'researched',
      properties: { score: 40, injection_observed: 'false' },
    })]} />);
    expect(screen.queryByText(/flagged the page/)).toBeNull();
    expect(screen.getByText(/scored this lead 40/)).toBeTruthy();
  });

  it('renders a researched row whose score cannot be converted to text', () => {
    // The ToPrimitive hazard, in the file that still had it: `String(x)` raises
    // TypeError on an object with a non-function `toString`, and every one of
    // these fields is agent-authored. A throw here takes out the whole lead
    // page, which is the bug the error boundary exists to soften and this
    // component exists not to cause.
    const hostile = JSON.parse('{"toString":"x"}');
    render(<Timeline events={[ev({
      event_type: 'researched',
      properties: { score: hostile, rationale: hostile, injection_observed: false },
    })]} />);
    expect(screen.getAllByText(/unreadable value/).length).toBeGreaterThan(0);
  });

  it('renders a note whose body cannot be converted to text', () => {
    render(<Timeline events={[ev({
      event_type: 'note',
      properties: { body: JSON.parse('{"toString":"x"}') },
    })]} />);
    expect(screen.getByText(/unreadable value/)).toBeTruthy();
  });

  it('renders a stage change whose new stage cannot be converted to text', () => {
    render(<Timeline events={[ev({
      event_type: 'stage_changed',
      properties: { diff: { stage: { before: 'sourced', after: JSON.parse('{"toString":"x"}') } } },
    })]} />);
    expect(screen.getByText(/moved to \(unreadable value\)/)).toBeTruthy();
  });

  it('still renders the ordinary rows unchanged', () => {
    render(<Timeline events={[
      ev({ id: 'a', event_type: 'note', properties: { body: 'called, left a voicemail' } }),
      ev({ id: 'b', event_type: 'stage_changed', properties: { diff: { stage: { after: 'contacted' } } } }),
      ev({ id: 'c', event_type: 'import', properties: {} }),
    ]} />);
    expect(screen.getByText(/called, left a voicemail/)).toBeTruthy();
    expect(screen.getByText(/moved to contacted/)).toBeTruthy();
    expect(screen.getByText(/imported from CSV/)).toBeTruthy();
  });
});
