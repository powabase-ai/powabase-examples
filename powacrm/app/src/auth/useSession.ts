import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from '@/lib/powabase';
import { queryClient } from '@/lib/queryClient';

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setLoading(false); });
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
