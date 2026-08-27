import { supabase } from './powabase';

// Every timeline write stamps who did it. Phase 1 has one GoTrue login per
// project, so the signed-in user's email is the most specific identity available
// client-side -- and it must come from the session, never a hardcoded name, or
// every copy of this app would attribute its timeline to whoever wrote the code.
// `getUser()` can fail (offline, expired token); a neutral label is better than
// blocking the event, which is advisory anyway.
export async function currentActorName(): Promise<string> {
  try {
    const { data, error } = await supabase.auth.getUser();
    if (error) return 'User';
    return data.user?.email ?? 'User';
  } catch {
    return 'User';
  }
}
