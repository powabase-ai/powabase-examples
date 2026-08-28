import type { CSSProperties } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { supabase } from '@/lib/powabase';
import { useBrand } from './BrandContext';
import { ErrorBoundary } from './ErrorBoundary';

const linkStyle = ({ isActive }: { isActive: boolean }): CSSProperties => ({
  display: 'block', padding: 'var(--space-1) var(--space-2)', borderRadius: 'var(--radius-md)',
  textDecoration: 'none', fontSize: 'var(--font-sm)',
  color: isActive ? 'var(--fg-primary)' : 'var(--fg-secondary)',
  background: isActive ? 'var(--hover)' : 'transparent',
});

export function Shell() {
  const { brand, brands, setBrandId } = useBrand();
  // The boundary that matters most is this one, INSIDE the chrome: a page that
  // throws leaves the nav, the brand picker and sign-out on screen, so the user
  // clicks their way out instead of reloading into the same broken route. App's
  // outer boundary is the backstop for a throw in the router or in Shell itself.
  const location = useLocation();
  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      <nav style={{ width: 240, flexShrink: 0, padding: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)', alignContent: 'start' }}>
        <select value={brand.id} onChange={e => setBrandId(e.target.value)}
          style={{ marginBottom: 'var(--space-4)', width: '100%' }}>
          {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <NavLink to="/" style={linkStyle} end>Pipeline</NavLink>
        <NavLink to="/import" style={linkStyle}>Import</NavLink>
        <NavLink to="/settings" style={linkStyle}>Settings</NavLink>
        <button onClick={() => supabase.auth.signOut()}
          style={{ marginTop: 'var(--space-6)', justifySelf: 'start' }}>Sign out</button>
      </nav>
      <main style={{ flex: 1, background: 'var(--bg-primary)', borderRadius: 'var(--radius-lg) 0 0 0',
        border: '1px solid var(--border-medium)', overflow: 'auto', padding: 'var(--space-6)' }}>
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
