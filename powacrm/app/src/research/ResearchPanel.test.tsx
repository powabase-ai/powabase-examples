// The payload in these tests is agent-authored, and the point of the file is
// that a WRONGLY TYPED field is not a crash.
//
// The bug this covers shipped: `const hooks = data.hooks ?? []` catches null and
// undefined but not a string, so `{"hooks": "none found"}` -- ordinary model
// output, and steerable by the prompt injection this feature anticipates --
// reached `hooks.map(...)` and threw during render. With no error boundary in
// the app at the time, React 19 unmounted the whole root: blank page, nav gone.
//
// Each case below renders the real panel with a hostile payload and asserts two
// things: it does not throw, and the good part of the report is still on screen.
// The second half is what stops the "fix" from degrading into rendering nothing.
import { describe, it, expect, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ResearchPanel } from './ResearchPanel';
import type { ResearchCompany, ResearchData } from './ResearchPanel';

afterEach(cleanup);

function company(research_data: unknown): ResearchCompany {
  return {
    id: 'co-1',
    name: 'Acme',
    domain: 'acme.example',
    research: 'summary text',
    research_data: research_data as ResearchData,
    tech_stack: null,
    researched_at: null,
  };
}

describe('ResearchPanel with an agent-authored payload', () => {
  it('renders a string `hooks` as no hooks instead of throwing', () => {
    render(<ResearchPanel company={company({
      summary: 'Acme sells widgets.',
      why_now: null,
      hooks: 'none found',
      sources: [],
    })} />);
    expect(screen.getByText('Acme sells widgets.')).toBeTruthy();
    expect(screen.queryByText('Hooks')).toBeNull();
    // Not silent: the reader is told a field came back malformed, because
    // "no hooks were found" and "the agent did not return a list" are
    // different facts and only one of them is worth a re-run.
    expect(screen.getByText(/unexpected shape/i).textContent).toContain('hooks');
  });

  it('survives every array field arriving as the wrong type at once', () => {
    render(<ResearchPanel company={company({
      summary: 'Still readable.',
      why_now: 'They raised a round.',
      tech_stack: 'React, Postgres',
      hooks: { hook: 'not an array' },
      sources: 42,
    })} />);
    expect(screen.getByText('Still readable.')).toBeTruthy();
    expect(screen.getByText('They raised a round.')).toBeTruthy();
    const warning = screen.getByText(/unexpected shape/i).textContent ?? '';
    expect(warning).toContain('tech_stack');
    expect(warning).toContain('hooks');
    expect(warning).toContain('sources');
  });

  it('renders hook entries that are missing fields, or hold the wrong types', () => {
    render(<ResearchPanel company={company({
      summary: 'Summary.',
      why_now: null,
      hooks: [
        { hook: 'Hiring SREs', evidence: 'careers page', source_url: 'https://acme.example/jobs' },
        { hook: 12345 },                                  // number, not string
        { hook: 'No evidence given', source_url: 42 },     // non-string url
        'a bare string in the array',
        null,
      ],
      sources: ['https://acme.example', 7],
    })} />);
    expect(screen.getByText('Hiring SREs')).toBeTruthy();
    expect(screen.getByText('12345')).toBeTruthy();
    // A non-string source_url must not become an href.
    expect(screen.getAllByRole('link').map(a => a.getAttribute('href')))
      .toEqual(['https://acme.example/jobs', 'https://acme.example', '7']);
    // The array fields were arrays, so nothing is reported as malformed.
    expect(screen.queryByText(/unexpected shape/i)).toBeNull();
  });

  it('shows the injection banner for a prose value, not just boolean true', () => {
    render(<ResearchPanel company={company({
      summary: 'Summary.',
      why_now: null,
      hooks: [],
      sources: [],
      // A model that DETECTED an injection and described it is the one input
      // guaranteed to be mishandled by a plain boolean cast. Fail toward the
      // warning: the cost of a spurious banner is far below the cost of a
      // missing one.
      injection_observed: 'detected on the pricing page',
    })} />);
    expect(screen.getByText(/attempted to instruct it/i)).toBeTruthy();
  });

  it('treats a research_data that is not an object at all as no research', () => {
    render(<ResearchPanel company={company('the whole payload is a string')} />);
    expect(screen.getByText('No research yet.')).toBeTruthy();
  });
});
