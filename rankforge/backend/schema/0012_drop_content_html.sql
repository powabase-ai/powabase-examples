-- 0012 — drop the dead articles.content_html column.
--
-- content_html was a cache of the rendered article HTML, written at publish time.
-- After the render-on-read change the column is never written or read: publish
-- renders HTML fresh from content_md (and the public page / webhook payload do the
-- same on each request), so the column only risked serving stale, un-sanitized
-- HTML. Removing it keeps content_md as the single source of truth.
--
-- Apply: psql "$POWABASE_DATABASE_URL" -f backend/schema/0012_drop_content_html.sql

begin;

alter table public.articles drop column if exists content_html;

notify pgrst, 'reload schema';

commit;
