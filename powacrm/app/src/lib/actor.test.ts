import { describe, it, expect, vi, beforeEach } from 'vitest';
import { currentActorName } from './actor';

// currentActorName must read the locally-cached session (getSession), never
// make a network call (getUser), and must never surface a full email address.
const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }));
vi.mock('./powabase', () => ({ supabase: { auth: { getSession } } }));

const mkSession = (user: object) => ({ data: { session: { user } }, error: null });

describe('currentActorName', () => {
  beforeEach(() => getSession.mockReset());

  it('prefers a display name from user_metadata over the email', async () => {
    getSession.mockResolvedValue(mkSession({ email: 'ana@example.com', user_metadata: { full_name: 'Ana Lee' } }));
    expect(await currentActorName()).toBe('Ana Lee');
  });

  it('falls back to the local part of the email when there is no metadata name', async () => {
    getSession.mockResolvedValue(mkSession({ email: 'ana@example.com', user_metadata: {} }));
    expect(await currentActorName()).toBe('ana');
  });

  it('falls back to the neutral label when there is no session', async () => {
    getSession.mockResolvedValue({ data: { session: null }, error: null });
    expect(await currentActorName()).toBe('User');
  });
});
