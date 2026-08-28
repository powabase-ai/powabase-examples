// The boundary had no test, which is the same gap that let the bug it exists to
// catch ship in the first place. Two things are covered here because both are
// load-bearing and neither is obvious from reading the component:
//
//   * getDerivedStateFromError actually catches, and the fallback it renders
//     cannot itself throw -- including for a thrown value that is not an Error.
//     That last case is the one with no backstop: the outermost boundary's
//     fallback throwing is exactly the blank document this component prevents.
//   * The resetKey latch clears on a new key and holds on the same one. A
//     boundary that never resets turns one broken page into a broken app by
//     every other route, and one that resets unconditionally re-renders the
//     component that just threw, forever.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ErrorBoundary, errorText } from './ErrorBoundary';

afterEach(cleanup);

// React logs every caught error to console.error, and so does componentDidCatch
// by design. Silenced so a passing run is readable; restored after each test.
beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); });

function Boom({ throws }: { throws: unknown }): React.ReactElement {
  throw throws;
}

const FALLBACK = /Something on this page broke/i;

describe('ErrorBoundary', () => {
  it('renders its children when nothing throws', () => {
    render(<ErrorBoundary><p>the pipeline</p></ErrorBoundary>);
    expect(screen.getByText('the pipeline')).toBeTruthy();
    expect(screen.queryByText(FALLBACK)).toBeNull();
  });

  it('catches a render throw and shows the error message', () => {
    render(<ErrorBoundary><Boom throws={new Error('hooks.map is not a function')} /></ErrorBoundary>);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
    expect(screen.getByText('hooks.map is not a function')).toBeTruthy();
    // The way out is part of the contract: a fallback with no route out is the
    // blank page with extra steps.
    expect(screen.getByText(/Back to the pipeline/i)).toBeTruthy();
  });

  it('survives a non-Error throw whose message is an object', () => {
    // Rendering `error.message` bare put an object in the React tree, which
    // throws -- inside the fallback, where the outer boundary would render the
    // identical fallback and throw identically.
    render(<ErrorBoundary><Boom throws={{ message: { nested: true } }} /></ErrorBoundary>);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
  });

  it('describes a thrown object that cannot be converted to text', () => {
    // String() runs ToPrimitive and raises on this one -- the same hole the
    // research panel had.
    //
    // ASSERTING THE MESSAGE, NOT THE HEADING. Fixed in review (round 3): this
    // test used to end at `getByText(FALLBACK)`, which was already true of the
    // old code and so guarded nothing. The old fallback rendered
    // `error.message` bare, and this value has no `message` -- so it read
    // `undefined`, React rendered nothing, and the heading was on screen with an
    // empty <pre> beneath it. The bug the test names is that the boundary cannot
    // DESCRIBE the value, and that is what is asserted now.
    render(<ErrorBoundary><Boom throws={JSON.parse('{"toString":"x"}')} /></ErrorBoundary>);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
    expect(screen.getByText('Something was thrown that cannot be described in text.')).toBeTruthy();
  });

  it('shows the fallback for `throw null`, which is legal and has no message', () => {
    // The reason state carries a `caught` flag instead of testing the value:
    // with `error !== null` as the condition this rendered the children again.
    render(<ErrorBoundary><Boom throws={null} /></ErrorBoundary>);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
  });

  it('clears on a new resetKey and holds on the same one', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="/leads/1"><Boom throws={new Error('bad lead')} /></ErrorBoundary>,
    );
    expect(screen.getByText(FALLBACK)).toBeTruthy();

    // Same key, healthy children: still latched. Without this the boundary would
    // re-render whatever threw on every parent update.
    rerender(<ErrorBoundary resetKey="/leads/1"><p>healthy</p></ErrorBoundary>);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
    expect(screen.queryByText('healthy')).toBeNull();

    // New key -- the route changed -- so the children get their chance back.
    rerender(<ErrorBoundary resetKey="/leads/2"><p>healthy</p></ErrorBoundary>);
    expect(screen.getByText('healthy')).toBeTruthy();
    expect(screen.queryByText(FALLBACK)).toBeNull();
  });

  it('latches again after a reset if the new route also throws', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="/a"><Boom throws={new Error('first')} /></ErrorBoundary>,
    );
    expect(screen.getByText('first')).toBeTruthy();
    rerender(<ErrorBoundary resetKey="/b"><Boom throws={new Error('second')} /></ErrorBoundary>);
    expect(screen.getByText('second')).toBeTruthy();
  });
});

describe('errorText', () => {
  it('reads an Error message', () => {
    expect(errorText(new Error('boom'))).toBe('boom');
  });
  it('reads a thrown string', () => {
    expect(errorText('just a string')).toBe('just a string');
  });
  it('describes a value with no message rather than rendering nothing', () => {
    expect(errorText(null)).toBe('null');
    expect(errorText(new Error(''))).toBe('No message was given.');
  });
  it('never returns a non-string, whatever the message is', () => {
    expect(typeof errorText({ message: { nested: true } })).toBe('string');
    expect(typeof errorText(JSON.parse('{"toString":"x"}'))).toBe('string');
  });
  it('survives a message getter that throws', () => {
    const hostile = {};
    Object.defineProperty(hostile, 'message', { get() { throw new Error('nope'); } });
    expect(errorText(hostile)).toBe('Something was thrown that cannot be described in text.');
  });
});
