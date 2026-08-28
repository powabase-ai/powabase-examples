import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { useSession } from '@/auth/useSession';
import { LoginPage } from '@/auth/LoginPage';
import { Shell } from '@/shell/Shell';
import { BrandProvider } from '@/shell/BrandContext';
import { BoardPage } from '@/board/BoardPage';
import { LeadPage } from '@/lead/LeadPage';
import { ImportPage } from '@/import/ImportPage';
import { SettingsPage } from '@/settings/SettingsPage';
import { queryClient } from '@/lib/queryClient';
import { ErrorBoundary } from '@/shell/ErrorBoundary';

// Inside BrowserRouter so it can watch the location: a boundary with no reset
// latches on the first throw and shows its fallback for every route afterwards,
// which turns one broken page into a broken app by a different route.
function RoutedApp() {
  const location = useLocation();
  return (
    <ErrorBoundary resetKey={location.pathname}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<BoardPage />} />
          <Route path="/leads/:id" element={<LeadPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </ErrorBoundary>
  );
}

export default function App() {
  const { session, loading } = useSession();
  if (loading) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* The outermost boundary, and it is not the same one as RoutedApp's.
            LoginPage and BrandProvider render OUTSIDE the routed tree, so a
            throw from either -- a malformed session, a brands query that returns
            something unexpected -- had no boundary above it at all and took the
            document with it.

            Keyed on the SESSION, not the route. There is no route change to
            observe at this level, but there is one transition that swaps the
            children entirely, and this boundary used to latch straight across
            it (fixed in review, round 3): LoginPage throws once, transiently;
            the magic-link callback lands; useSession yields a session; App
            re-renders with a completely different tree -- and the fallback was
            still there, recoverable only by Reload. "The fallback's reload is
            the honest recovery" is a fair argument for a BrandProvider that
            cannot mount. It is not one for a sign-in the user has already
            completed successfully. */}
        <ErrorBoundary resetKey={session ? 'authed' : 'anon'}>
          {!session ? <LoginPage /> : (
            <BrandProvider>
              <RoutedApp />
            </BrandProvider>
          )}
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
