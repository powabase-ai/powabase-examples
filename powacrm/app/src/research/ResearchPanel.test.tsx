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

  // injectionObserved() has to agree with complete_research_job byte for byte,
  // or the banner and the stored flag say different things about the same run.
  // The rule both sides implement: explicit false (boolean, or the string
  // "false") is false; absent, null and an empty string carry no claim and are
  // false; everything else is true.
  describe('injection_observed', () => {
    const withFlag = (injection_observed: unknown) =>
      company({ summary: 'Summary.', why_now: null, hooks: [], sources: [], injection_observed });

    it('shows the banner for a prose value, not just boolean true', () => {
      // A model that DETECTED an injection and described it in words is the one
      // input guaranteed to be mishandled by a plain boolean cast, and it is the
      // server-side half of this fix (complete_research_job used to coerce it to
      // FALSE). Note for anyone auditing this file: this case ALSO passes
      // against the old panel, because a non-empty string was already truthy.
      // It is a regression guard. The falsifying case is the next one.
      render(<ResearchPanel company={withFlag('detected on the pricing page')} />);
      expect(screen.getByText(/attempted to instruct it/i)).toBeTruthy();
    });

    it('does NOT show the banner for the string "false"', () => {
      // This is the case that fails against the old `data.injection_observed &&`
      // truthiness check: "false" is a non-empty string, so the old panel
      // rendered the warning for a payload that explicitly said there was
      // nothing to warn about -- while the server, which casts it, stored false.
      render(<ResearchPanel company={withFlag('false')} />);
      expect(screen.queryByText(/attempted to instruct it/i)).toBeNull();
    });

    it('treats an empty string as absent, the same as the server does', () => {
      // `''::boolean` raises in Postgres, so this used to be the one value where
      // the two sides disagreed: the server coerced it to true, the client read
      // it as false. Both now treat it as no claim at all.
      render(<ResearchPanel company={withFlag('   ')} />);
      expect(screen.queryByText(/attempted to instruct it/i)).toBeNull();
    });
  });

  it('treats a research_data that is not an object at all as no research', () => {
    render(<ResearchPanel company={company('the whole payload is a string')} />);
    expect(screen.getByText('No research yet.')).toBeTruthy();
  });

  it('renders a payload whose fields cannot be converted to text at all', () => {
    // The exact payload that reached this component through the RPC, and the
    // one String() cannot survive: `String(x)` runs ToPrimitive, so an object
    // carrying a non-function `toString` raises
    // `TypeError: Cannot convert object to primitive value` -- the defence this
    // file names in its own header. complete_research_job accepted it because
    // it validated `summary` with `->>`, which returns an OBJECT's text and is
    // therefore non-empty. 0012 now requires a string summary; this is the
    // client half, and it also covers `hooks[].hook`, which no server-side
    // check types.
    const hostile = JSON.parse('{"toString":"x"}');
    render(<ResearchPanel company={company({
      summary: hostile,
      why_now: null,
      tech_stack: [hostile],
      hooks: [{ hook: hostile, evidence: hostile }],
      sources: [],
    })} />);
    // The panel is on screen rather than the boundary's fallback, and it says
    // the value was unreadable instead of leaving a blank line.
    expect(screen.getAllByText('(unreadable value)').length).toBeGreaterThan(0);
    expect(screen.getByText('Hooks')).toBeTruthy();
  });

  it('renders duplicate tech-stack values instead of dropping one', () => {
    // `key={t}` collided on ["React","React"] -- ordinary model output.
    render(<ResearchPanel company={company({
      summary: 'Summary.', why_now: null, tech_stack: ['React', 'React', 'Postgres'], hooks: [], sources: [],
    })} />);
    expect(screen.getAllByText('React').length).toBe(2);
  });
});
