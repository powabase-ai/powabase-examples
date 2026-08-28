import { QueryClient } from '@tanstack/react-query';

// One client for the app's lifetime. It lives here rather than in App.tsx so
// that useSession -- which is mounted OUTSIDE QueryClientProvider and so cannot
// call useQueryClient() -- can still reach it to wipe the cache on sign-out.
//
// That wipe is load-bearing on a shared machine. Without it the next person to
// sign in is served the previous user's ['brands'] and ['leads', ...] entries
// synchronously from cache, and sees their brand name, lead names and emails
// until the background refetch returns. RLS is not defeated -- the refetch
// returns the correct rows -- but the stale render is real disclosure, and it
// contradicts the isolation the README promises.
export const queryClient = new QueryClient();
