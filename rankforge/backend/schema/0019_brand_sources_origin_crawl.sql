-- 0019 — allow 'crawl' as a brand_sources origin.
--
-- M6's first cut only knew sitemap + manual URLs. Materials ingest now also
-- discovers pages by crawling a site (Powabase import-url mode=crawl), which tags
-- rows origin='crawl'. The old CHECK rejected that value, so every crawl ingest
-- failed with "violates check constraint brand_sources_origin_check".
--
-- Apply: uv run python scripts/apply_schema.py schema/0019_brand_sources_origin_crawl.sql

begin;

alter table public.brand_sources
    drop constraint if exists brand_sources_origin_check;
alter table public.brand_sources
    add constraint brand_sources_origin_check
    check (origin in ('sitemap', 'manual', 'crawl'));

commit;
