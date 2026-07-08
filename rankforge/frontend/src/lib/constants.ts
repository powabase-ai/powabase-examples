export const LAST_BRAND_KEY = "rankforge:lastBrand";
// A teammate invite token stashed from an /accept-invite link so it survives the
// sign-in / sign-up round-trip and can be resumed after auth.
export const PENDING_INVITE_KEY = "rankforge:pendingInvite";

// Absolute origin of the deployed app, used as the metadataBase so Open Graph /
// Twitter image URLs and canonicals resolve to absolute URLs (crawlers and social
// scrapers reject relative ones). Set NEXT_PUBLIC_SITE_URL in production; the
// localhost fallback keeps dev working. Trailing slash trimmed for clean joins.
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3007"
).replace(/\/+$/, "");
