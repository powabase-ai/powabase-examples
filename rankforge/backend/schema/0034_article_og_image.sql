-- Per-article Open Graph / social-share image.
--
-- When set, it's an uploaded image in Powabase public storage (bucket 'og-images',
-- namespaced by org) that OVERRIDES the dynamically-generated OG card on the public
-- /p/{id} page's metadata. When null, the page falls back to a per-article card
-- rendered on the fly by the frontend (Next.js ImageResponse).
alter table public.articles
    add column if not exists og_image_url text;
