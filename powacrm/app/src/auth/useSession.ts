import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from '@/lib/powabase';
import { queryClient } from '@/lib/queryClient';

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    // setLoading(false) has to happen on BOTH paths. getSession() rejects when the
    // browser denies storage access -- Safari private browsing, or site data
    // blocked -- and App renders null while loading, so a rejection here left a
    // permanently blank page with an unhandled rejection and no way back.
    supabase.auth.getSession()
      .then(({ data }) => setSession(data.session))
      .catch(err => { console.error('Could not read the stored session', err); setSession(null); })
      .finally(() => setLoading(false));
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      // Drop every cached row on the way out. The next sign-in on this browser
      // must not be served the previous account's brands or leads from cache
      // while its own refetch is still in flight.
      if (event === 'SIGNED_OUT') queryClient.clear();
      setSession(s);
    });
    return () => sub.subscription.unsubscribe();
  }, []);
  return { session, loading };
}
