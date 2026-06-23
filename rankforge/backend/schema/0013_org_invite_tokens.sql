-- 0013 — secret tokens for org invites (close the invite-hijack vector).
--
-- 0011 let a teammate auto-join an org on first sign-in by MATCHING THEIR EMAIL.
-- That trusts the GoTrue `email` claim to prove ownership — but when the project
-- auto-confirms signups (no email verification), anyone can register with a
-- victim's invited email and claim the invite. We switch to an explicit,
-- token-authorized accept step: the invite carries an unguessable token (shared
-- by the admin out-of-band); joining requires presenting it while signed in. The
-- email is now only a human label, never an authorization signal.
--
-- Apply: uv run python scripts/apply_schema.py schema/0013_org_invite_tokens.sql

begin;

alter table public.org_invites add column if not exists token text;

-- Backfill any pre-existing pending invites with a random token (no pgcrypto
-- dependency — two UUIDs give 64 hex chars of entropy).
update public.org_invites
   set token = replace(gen_random_uuid()::text, '-', '')
             || replace(gen_random_uuid()::text, '-', '')
 where token is null;

create unique index if not exists org_invites_token_idx
    on public.org_invites (token);

notify pgrst, 'reload schema';

commit;
