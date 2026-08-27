import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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

export default function App() {
  const { session, loading } = useSession();
  if (loading) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {!session ? <LoginPage /> : (
          <BrandProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route path="/" element={<BoardPage />} />
                <Route path="/leads/:id" element={<LeadPage />} />
                <Route path="/import" element={<ImportPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </BrandProvider>
        )}
      </BrowserRouter>
    </QueryClientProvider>
  );
}
