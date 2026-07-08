/**
 * Minimal GoTrue (Supabase-compatible) auth client.
 *
 * The browser holds only the project's Anon key — never a service-role secret.
 * We call the project's `/auth/v1/*` endpoints directly; the resulting access
 * token (a JWT signed with the project's JWT secret) is sent to the RankForge
 * backend as `Authorization: Bearer <token>`, where it is verified.
 */

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export interface GoTrueUser {
  id: string;
  email?: string;
}

export interface Session {
  access_token: string;
  refresh_token: string;
  expires_at?: number; // unix seconds
  token_type?: string;
  user: GoTrueUser;
}

async function authPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${URL}/auth/v1/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: ANON,
      Authorization: `Bearer ${ANON}`,
    },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      data.error_description || data.msg || data.error ||
      `Authentication failed (${res.status})`;
    throw new Error(msg);
  }
  return data as T;
}

export function signInWithPassword(email: string, password: string): Promise<Session> {
  return authPost<Session>("token?grant_type=password", { email, password });
}

/** Returns a Session if the project auto-confirms, else an object with no access_token. */
export function signUp(
  email: string,
  password: string
): Promise<Partial<Session> & { user?: GoTrueUser }> {
  return authPost<Partial<Session> & { user?: GoTrueUser }>("signup", {
    email,
    password,
  });
}

export function refreshGrant(refreshToken: string): Promise<Session> {
  return authPost<Session>("token?grant_type=refresh_token", {
    refresh_token: refreshToken,
  });
}

/** Set a new password for the signed-in user (requires a valid access token).
 *  GoTrue's PUT /user doesn't itself require the old password — callers verify the
 *  current password first (via signInWithPassword) as an app-level guard. */
export async function updatePassword(
  accessToken: string,
  password: string
): Promise<GoTrueUser> {
  const res = await fetch(`${URL}/auth/v1/user`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      apikey: ANON,
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      data.error_description || data.msg || data.error ||
      `Password update failed (${res.status})`;
    throw new Error(msg);
  }
  return data as GoTrueUser;
}

export async function signOutRequest(accessToken: string): Promise<void> {
  await fetch(`${URL}/auth/v1/logout`, {
    method: "POST",
    headers: { apikey: ANON, Authorization: `Bearer ${accessToken}` },
  }).catch(() => {
    /* best-effort; local session is cleared regardless */
  });
}
