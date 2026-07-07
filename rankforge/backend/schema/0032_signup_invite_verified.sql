-- Gate app access behind a one-time signup invite code.
--
-- Public registration stays open (GoTrue), but a newly-registered account cannot use the
-- app until it redeems the shared signup invite code ONCE (POST /api/auth/redeem-invite),
-- which flips this flag. The gate is enforced server-side in auth.get_current_user and is
-- only active when SIGNUP_INVITE_CODE is configured, so local/dev without the env var is
-- unaffected.
--
-- Existing accounts are grandfathered to TRUE so nobody currently using the app is locked
-- out; only signups after this migration start unverified (column default false).
--
-- Apply: uv run python scripts/apply_schema.py schema/0032_signup_invite_verified.sql

begin;

alter table public.profiles
    add column if not exists invite_verified boolean not null default false;

-- Grandfather every account that already exists at deploy time.
update public.profiles set invite_verified = true where invite_verified = false;

notify pgrst, 'reload schema';

commit;
