-- Brand logo shown in the workspace switcher. Stored as a small downscaled data URL
-- (or an http(s) URL) — no separate object storage needed for a workspace avatar.
alter table public.business_profiles
    add column if not exists logo_url text;
