// The OUTER boundary -- the one wrapped around LoginPage and BrandProvider,
// outside the routed tree -- used to have no resetKey at all, on the argument
// that there is no route change to key on at that level and a reload is the
// honest recovery for a provider that cannot mount.
//
// That argument holds for BrandProvider. It does not hold for LoginPage, and
// this file is why: sign-in swaps the entire subtree, and a latched boundary
// sat over the top of a session the user had already successfully obtained.
// The only way out was the Reload button, on a page whose problem had already
// resolved itself.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { Session } from '@supabase/supabase-js';

// Everything below App imports the supabase client, which throws at module load
// without the VITE_* env vars. None of it is exercised here -- the assertions
// are all about which of the two top-level branches renders -- so it is stubbed
// at the module boundary rather than configured.
vi.mock('@/lib/powabase', () => ({ supabase: {} }));
vi.mock('@/lib/queryClient', async () => {
  const { QueryClient } = await import('@tanstack/react-query');
  return { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) };
});

let sessionValue: Session | null = null;
vi.mock('@/auth/useSession', () => ({
  useSession: () => ({ session: sessionValue, loading: false }),
}));

// LoginPage throws while the test says to. The flag is flipped by the test
// rather than by the first render, because React 19 re-renders a subtree once
// more after a boundary catches it in development, and a self-clearing flag
// would make the fixture depend on how many of those retries happen.
let loginThrows = true;
vi.mock('@/auth/LoginPage', () => ({
  LoginPage: () => {
    if (loginThrows) throw new Error('the sign-in form blew up');
    return <p>sign in</p>;
  },
}));

vi.mock('@/shell/BrandContext', () => ({
  BrandProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/board/BoardPage', () => ({ BoardPage: () => <p>the pipeline board</p> }));
vi.mock('@/shell/Shell', async () => {
  const { Outlet } = await import('react-router-dom');
  return { Shell: () => <Outlet /> };
});
vi.mock('@/lead/LeadPage', () => ({ LeadPage: () => null }));
vi.mock('@/import/ImportPage', () => ({ ImportPage: () => null }));
vi.mock('@/settings/SettingsPage', () => ({ SettingsPage: () => null }));

const FALLBACK = /Something on this page broke/i;

afterEach(cleanup);
beforeEach(() => {
  sessionValue = null;
  loginThrows = true;
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => { vi.restoreAllMocks(); });

describe('App', () => {
  it('clears the outer boundary once a sign-in lands', async () => {
    const { default: App } = await import('./App');

    const { rerender } = render(<App />);
    // The transient throw from LoginPage is caught rather than blanking the app.
    expect(screen.getByText(FALLBACK)).toBeTruthy();

    // The magic-link callback lands and useSession yields a session, so App
    // re-renders with an entirely different tree. Without a session-derived
    // resetKey the boundary stays latched and the user is still looking at the
    // fallback for a failure that no longer exists.
    sessionValue = { user: { id: 'u1' } } as unknown as Session;
    rerender(<App />);

    expect(screen.queryByText(FALLBACK)).toBeNull();
    expect(screen.getByText('the pipeline board')).toBeTruthy();
  });

  it('holds the fallback while the session is unchanged', async () => {
    const { default: App } = await import('./App');

    const { rerender } = render(<App />);
    expect(screen.getByText(FALLBACK)).toBeTruthy();

    // The other half of the contract, and the reason the key is the session
    // rather than something that changes on every pass: a boundary that resets
    // unconditionally re-mounts the component that just threw, forever. Even
    // with LoginPage now willing to render, an unchanged session must not clear
    // it -- only the user's own Reload does.
    loginThrows = false;
    rerender(<App />);
    expect(screen.getByText(FALLBACK)).toBeTruthy();
    expect(screen.queryByText('sign in')).toBeNull();
  });
});
