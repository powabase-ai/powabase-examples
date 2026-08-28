import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

// The only thing standing between one bad render and a blank document.
//
// React 19 unmounts the entire root when a render throws with no boundary above
// it -- not just the offending subtree. There was no boundary anywhere in this
// app, and one agent-authored field was enough to trigger it: a research payload
// with `"hooks": "none found"` reached `hooks.map(...)` in ResearchPanel and
// threw, taking the nav with it. The user got a white page with no message and
// no route to anywhere, and a reload landed on the same lead and did it again.
//
// This is the backstop, not the fix. The fix is that components validate the
// data they render (ResearchPanel now does, and so does complete_research_job
// server-side). A boundary that catches a bug turns "unusable app" into "one
// broken page with a way out", which is the difference worth having on every
// render path, including the ones nobody has thought of yet.
//
// Class component because that is still the only way: React exposes no hook for
// componentDidCatch. `resetKey` lets the caller clear the error when the route
// changes, so navigating away from the broken page actually works -- without it
// the boundary stays latched and every subsequent route renders the fallback.
type Props = { children: ReactNode; resetKey?: unknown };
// `caught` is a separate flag rather than `error !== null` because a throw does
// not have to throw an Error, or anything at all: `throw null` and
// `throw undefined` are legal, and keying "is there an error" off the value
// itself would leave the boundary rendering the children that just threw.
type State = { caught: boolean; error: unknown; resetKey?: unknown };

// WHAT A THROWN VALUE IS ALLOWED TO BE: anything. `throw {message: {}}` is legal
// JavaScript, and rendering that `.message` puts an object in the tree, which
// React throws on -- inside the fallback of the outermost boundary, where there
// is nothing left to catch it and the blank page this component exists to
// prevent comes back by the one route it does not cover. `String()` is not the
// answer on its own either: it runs ToPrimitive and raises on an object with a
// non-function `toString` (see src/lib/asText.ts). Exported for its test.
export function errorText(error: unknown): string {
  try {
    const raw = error !== null && typeof error === 'object' && 'message' in error
      ? (error as { message: unknown }).message
      : error;
    if (typeof raw === 'string') return raw === '' ? 'No message was given.' : raw;
    const text = String(raw);
    return text === '' ? 'No message was given.' : text;
  } catch {
    // A getter that throws, or a value that cannot be converted to text at all.
    return 'Something was thrown that cannot be described in text.';
  }
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { caught: false, error: null };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { caught: true, error };
  }

  // Derived-state reset rather than componentDidUpdate: this runs during the
  // render that brings the new key in, so the children are given a chance to
  // render on the very same pass instead of flashing the fallback first.
  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (state.caught && props.resetKey !== state.resetKey) {
      return { caught: false, error: null, resetKey: props.resetKey };
    }
    if (state.resetKey !== props.resetKey) return { resetKey: props.resetKey };
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // The console is the only sink this app has -- there is no backend to report
    // to, by design. Logged with the component stack because "hooks.map is not a
    // function" without one does not say which panel threw.
    console.error('PowaCRM: a render failed and was caught by the error boundary', error, info.componentStack);
  }

  render() {
    if (!this.state.caught) return this.props.children;
    return (
      <div style={{ padding: 'var(--space-6)', display: 'grid', gap: 'var(--space-3)', justifyItems: 'start' }}>
        <h1 style={{ fontSize: 'var(--font-lg)', margin: 0 }}>Something on this page broke</h1>
        <p style={{ color: 'var(--fg-secondary)', fontSize: 'var(--font-sm)', margin: 0 }}>
          Nothing was lost. If this page keeps failing, the data behind it is probably
          malformed rather than missing — the details are in the browser console.
        </p>
        <pre style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)', whiteSpace: 'pre-wrap', margin: 0 }}>
          {errorText(this.state.error)}
        </pre>
        {/* A plain anchor and a reload, not router navigation: this component is
            mounted in two places (around Shell's Outlet and around the whole
            route tree), and one of them catches a throw from the router subtree
            itself. Neither recovery may depend on the thing that just failed. */}
        <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
          <a href="/">← Back to the pipeline</a>
          <button onClick={() => window.location.reload()}>Reload</button>
        </div>
      </div>
    );
  }
}
