import { supabase } from './powabase';

// Every timeline write stamps who did it. Phase 1 has one GoTrue login per
// project, so the signed-in user is the most specific identity available
// client-side -- and it must come from the session, never a hardcoded name, or
// every copy of this app would attribute its timeline to whoever wrote the code.
// `getSession()` reads the locally-cached session (no network round-trip),
// unlike `getUser()` which re-validates against GoTrue on every call -- this
// runs on every note and every stage change, so it must stay local.
// Prefer a real display name from user_metadata; fall back to the local part
// of the email (never the full address -- `events.actor_name` is meant to be
// human-readable, and the full email is PII this table shouldn't carry) and
// finally to a neutral label if there is no session at all. This can't throw
// in normal operation, but a neutral label is better than blocking the event,
// which is advisory anyway.
export async function currentActorName(): Promise<string> {
  try {
    const { data, error } = await supabase.auth.getSession();
    if (error || !data.session) return 'User';
    const metadata = data.session.user.user_metadata as Record<string, unknown> | undefined;
    const metaName = metadata?.full_name ?? metadata?.name;
    if (typeof metaName === 'string' && metaName.trim()) return metaName;
    const email = data.session.user.email;
    const localPart = email?.split('@')[0];
    return localPart || 'User';
  } catch {
    return 'User';
  }
}
